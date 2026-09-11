"""SQLite storage layer for favorites, ratings, comments, and price history."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "data" / "favorites.db"

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS favorites (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_key TEXT    UNIQUE  NOT NULL,
    title       TEXT    NOT NULL DEFAULT '',
    price       INTEGER,
    currency    TEXT    NOT NULL DEFAULT 'тг',
    rooms       INTEGER,
    area        REAL,
    floor       INTEGER,
    total_floors INTEGER,
    address     TEXT    NOT NULL DEFAULT '',
    url         TEXT    NOT NULL DEFAULT '',
    source      TEXT    NOT NULL DEFAULT '',
    phone       TEXT    NOT NULL DEFAULT '',
    description TEXT    NOT NULL DEFAULT '',
    photo       TEXT    NOT NULL DEFAULT '',
    date_published TEXT NOT NULL DEFAULT '',
    date_updated   TEXT NOT NULL DEFAULT '',
    rating      INTEGER NOT NULL DEFAULT 0,
    comment     TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_key TEXT    NOT NULL,
    price       INTEGER,
    currency    TEXT    NOT NULL DEFAULT 'тг',
    checked_at TEXT    NOT NULL,
    FOREIGN KEY (listing_key) REFERENCES favorites(listing_key)
);

CREATE INDEX IF NOT EXISTS idx_price_history_key
    ON price_history(listing_key, checked_at);

CREATE TABLE IF NOT EXISTS buildings (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    building_id       TEXT    UNIQUE  NOT NULL,
    address           TEXT    NOT NULL DEFAULT '',
    street            TEXT    NOT NULL DEFAULT '',
    house_number      TEXT    NOT NULL DEFAULT '',
    lat               REAL,
    lon               REAL,
    floors_total      INTEGER,
    building_material TEXT    NOT NULL DEFAULT '',
    district          TEXT    NOT NULL DEFAULT '',
    microdistrict    TEXT    NOT NULL DEFAULT '',
    residential_complex TEXT NOT NULL DEFAULT '',
    year_built        TEXT    NOT NULL DEFAULT '',
    updated_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS organizations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id        TEXT    UNIQUE  NOT NULL,
    name          TEXT    NOT NULL DEFAULT '',
    rubrics       TEXT    NOT NULL DEFAULT '',
    rating        REAL,
    review_count  INTEGER,
    address       TEXT    NOT NULL DEFAULT '',
    lat           REAL,
    lon           REAL,
    website       TEXT    NOT NULL DEFAULT '',
    phones        TEXT    NOT NULL DEFAULT '',
    parent_org_id TEXT    NOT NULL DEFAULT '',
    branch_count  INTEGER,
    updated_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_organizations_phone
    ON organizations(phones);
CREATE INDEX IF NOT EXISTS idx_buildings_complex
    ON buildings(residential_complex);

CREATE TABLE IF NOT EXISTS bot_users (
    telegram_id   INTEGER PRIMARY KEY,
    username      TEXT    NOT NULL DEFAULT '',
    first_name    TEXT    NOT NULL DEFAULT '',
    role          TEXT    NOT NULL DEFAULT 'user',
    searches      INTEGER NOT NULL DEFAULT 0,
    searches_today INTEGER NOT NULL DEFAULT 0,
    last_search_day TEXT  NOT NULL DEFAULT '',
    joined_at     TEXT    NOT NULL,
    last_seen     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_subscription_requests (
    telegram_id INTEGER PRIMARY KEY,
    username    TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'pending',
    requested_at TEXT   NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_support_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    username    TEXT    NOT NULL DEFAULT '',
    text        TEXT    NOT NULL DEFAULT '',
    answered    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);
"""

# Columns added after the initial schema. Applied on connect so older
# database files created before these fields existed are upgraded in place.
_MIGRATIONS = [
    ("favorites", "date_published", "TEXT NOT NULL DEFAULT ''"),
    ("favorites", "date_updated", "TEXT NOT NULL DEFAULT ''"),
    ("favorites", "lat", "REAL"),
    ("favorites", "lon", "REAL"),
    # Extended fields surfaced from 2GIS-first extraction (task_2gis_2do.md §5).
    ("favorites", "listing_type",        "TEXT NOT NULL DEFAULT ''"),
    ("favorites", "rental_period",       "TEXT NOT NULL DEFAULT ''"),
    ("favorites", "deposit",             "INTEGER"),
    ("favorites", "commission_percent",  "INTEGER"),
    ("favorites", "utilities",           "TEXT NOT NULL DEFAULT ''"),
    ("favorites", "building_id",         "TEXT NOT NULL DEFAULT ''"),
    ("favorites", "residential_complex", "TEXT NOT NULL DEFAULT ''"),
    ("favorites", "provider",            "TEXT NOT NULL DEFAULT ''"),
    ("favorites", "owner_probability",   "REAL"),
    ("favorites", "quality_score",       "INTEGER"),
    ("favorites", "duplicate_group_id",  "TEXT NOT NULL DEFAULT ''"),
    ("favorites", "check_status",        "TEXT NOT NULL DEFAULT ''"),
    # Telegram bot: daily search quota (non-admin users).
    ("bot_users", "searches_today",   "INTEGER NOT NULL DEFAULT 0"),
    ("bot_users", "last_search_day",  "TEXT NOT NULL DEFAULT ''"),
]


def _apply_migrations(conn):
    for table, col, decl in _MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if cols and col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def listing_key(url: str, source: str, title: str = "") -> str:
    raw = f"{source}|{url}|{title}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def _today() -> str:
    """Local calendar day (ISO) — the seam for daily-quota tests."""
    return datetime.now().date().isoformat()


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        # Double-checked locking: without the inner guard, two threads racing
        # here would each create a connection and the loser's would leak.
        with _lock:
            if _conn is None:
                DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
                _conn.row_factory = sqlite3.Row
                _conn.execute("PRAGMA journal_mode=WAL")
                _conn.executescript(SCHEMA)
                _apply_migrations(_conn)
                _conn.commit()
    return _conn


@contextmanager
def db_cursor():
    conn = get_conn()
    with _lock:
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise


# ============================================================
# Favorites CRUD
# ============================================================

def add_favorite(data: dict) -> dict:
    key = listing_key(data.get("url", ""), data.get("source", ""), data.get("title", ""))
    now = _now()
    row = {
        "listing_key": key,
        "title": data.get("title", ""),
        "price": data.get("price"),
        "currency": data.get("currency", "тг"),
        "rooms": data.get("rooms"),
        "area": data.get("area"),
        "floor": data.get("floor"),
        "total_floors": data.get("total_floors"),
        "address": data.get("address", ""),
        "url": data.get("url", ""),
        "source": data.get("source", ""),
        "phone": data.get("phone", ""),
        "description": data.get("description", ""),
        "photo": data.get("photo", ""),
        "lat": data.get("lat"),
        "lon": data.get("lon"),
        "date_published": data.get("date_published", ""),
        "date_updated": data.get("date_updated", ""),
        "rating": int(data.get("rating", 0) or 0),
        "comment": data.get("comment", ""),
        "created_at": now,
        "updated_at": now,
    }
    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO favorites
                (listing_key, title, price, currency, rooms, area, floor,
                 total_floors, address, url, source, phone, description, photo,
                 lat, lon, date_published, date_updated, rating, comment, created_at, updated_at)
            VALUES
                (:listing_key, :title, :price, :currency, :rooms, :area, :floor,
                 :total_floors, :address, :url, :source, :phone, :description, :photo,
                 :lat, :lon, :date_published, :date_updated, :rating, :comment, :created_at, :updated_at)
        """, row)
        fav_id = cur.lastrowid
        if row["price"] is not None:
            cur.execute("""
                INSERT INTO price_history (listing_key, price, currency, checked_at)
                VALUES (:listing_key, :price, :currency, :checked_at)
            """, {"listing_key": key, "price": row["price"],
                  "currency": row["currency"], "checked_at": now})
    row["id"] = fav_id
    return row


def get_favorite(listing_key: str) -> Optional[dict]:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM favorites WHERE listing_key = ?", (listing_key,))
        r = cur.fetchone()
        return dict(r) if r else None


def get_favorite_by_id(fav_id: int) -> Optional[dict]:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM favorites WHERE id = ?", (fav_id,))
        r = cur.fetchone()
        return dict(r) if r else None


def list_favorites() -> list[dict]:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM favorites ORDER BY rating DESC, updated_at DESC")
        return [dict(r) for r in cur.fetchall()]


def update_rating(listing_key: str, rating: int) -> Optional[dict]:
    if not (0 <= rating <= 5):
        raise ValueError("Rating must be 0-5")
    now = _now()
    with db_cursor() as cur:
        cur.execute(
            "UPDATE favorites SET rating = ?, updated_at = ? WHERE listing_key = ?",
            (rating, now, listing_key),
        )
        if cur.rowcount == 0:
            return None
    return get_favorite(listing_key)


def update_comment(listing_key: str, comment: str) -> Optional[dict]:
    now = _now()
    with db_cursor() as cur:
        cur.execute(
            "UPDATE favorites SET comment = ?, updated_at = ? WHERE listing_key = ?",
            (comment, now, listing_key),
        )
        if cur.rowcount == 0:
            return None
    return get_favorite(listing_key)


def update_price(listing_key: str, price: Optional[int], currency: str = "тг") -> Optional[dict]:
    now = _now()
    with db_cursor() as cur:
        cur.execute(
            "UPDATE favorites SET price = ?, currency = ?, updated_at = ? WHERE listing_key = ?",
            (price, currency, now, listing_key),
        )
        if cur.rowcount == 0:
            return None
        if price is not None:
            cur.execute("""
                INSERT INTO price_history (listing_key, price, currency, checked_at)
                VALUES (?, ?, ?, ?)
            """, (listing_key, price, currency, now))
    return get_favorite(listing_key)


def update_phone(listing_key: str, phone: str) -> None:
    """Update the contact phone on a favorite (from re-parsed detail page)."""
    with db_cursor() as cur:
        cur.execute(
            "UPDATE favorites SET phone = ? WHERE listing_key = ?",
            (phone, listing_key),
        )


def update_check_status(listing_key: str, status: str) -> None:
    """Store the listing status shown on the detail page (e.g. "В архиве")."""
    with db_cursor() as cur:
        cur.execute(
            "UPDATE favorites SET check_status = ? WHERE listing_key = ?",
            (status, listing_key),
        )


def update_coords(listing_key: str, lat: Optional[float], lon: Optional[float]) -> None:
    """Update lat/lon on a favorite (from re-parsed detail page)."""
    with db_cursor() as cur:
        cur.execute(
            "UPDATE favorites SET lat = ?, lon = ? WHERE listing_key = ?",
            (lat, lon, listing_key),
        )


def delete_favorite(listing_key: str) -> bool:
    with db_cursor() as cur:
        cur.execute("DELETE FROM price_history WHERE listing_key = ?", (listing_key,))
        cur.execute("DELETE FROM favorites WHERE listing_key = ?", (listing_key,))
        return cur.rowcount > 0


def clear_favorites() -> int:
    """Delete every favorite and its price history. Returns count removed."""
    with db_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS c FROM favorites")
        count = cur.fetchone()["c"]
        cur.execute("DELETE FROM price_history")
        cur.execute("DELETE FROM favorites")
        return count


def clear_all() -> None:
    """Wipe all data from all tables but keep the schema (no file delete)."""
    with db_cursor() as cur:
        cur.execute("DELETE FROM price_history")
        cur.execute("DELETE FROM favorites")
        cur.execute("DELETE FROM buildings")
        cur.execute("DELETE FROM organizations")


# ============================================================
# Price history
# ============================================================

def get_price_history(listing_key: str) -> list[dict]:
    with db_cursor() as cur:
        cur.execute("""
            SELECT * FROM price_history
            WHERE listing_key = ?
            ORDER BY checked_at ASC
        """, (listing_key,))
        return [dict(r) for r in cur.fetchall()]


def get_all_price_history() -> dict[str, list[dict]]:
    favs = list_favorites()
    result = {}
    for f in favs:
        result[f["listing_key"]] = get_price_history(f["listing_key"])
    return result


def check_prices() -> list[dict]:
    """Re-parse URLs of all saved favorites and record current prices.

    Every favorite is represented in the result list (checked == len(favs)).
    A listing is only accepted when its URL matches the favorite URL — detail
    pages can contain recommendation cards of *other* listings, so taking
    ``results[0]`` blindly could record a foreign price.
    """
    import random
    import time

    from parsers.factory import get_all_parsers
    from parsers.models import SearchParams

    def _entry(fav, new_price, changed, error=None, status=None):
        item = {
            "listing_key": fav["listing_key"],
            "title": fav["title"],
            "old_price": fav["price"],
            "new_price": new_price,
            "changed": changed,
            "status": status or "",
        }
        if error:
            item["error"] = error
        return item

    parsers = {p.name: p for p in get_all_parsers()}
    favs = list_favorites()
    updated = []
    for fav in favs:
        url = fav["url"]
        source = fav["source"]
        if not url or not source:
            updated.append(_entry(fav, None, False, "нет URL"))
            continue
        parser = parsers.get(source)
        if parser is None:
            updated.append(_entry(fav, None, False, f"нет парсера для {source}"))
            continue
        try:
            # BaseParser.fetch: randomized anti-bot headers, SSL/403 retries,
            # encoding fixes — instead of a bare requests.get.
            html = parser.fetch(url)
            # Detect a status shown on the detail page (e.g. krisha "В
            # архиве" / "Объявление может быть неактуальным"). Persist it on
            # the favorite so the card can show it (survives reloads).
            status = parser.detect_status(html) or ""
            if status != (fav.get("check_status") or ""):
                update_check_status(fav["listing_key"], status)
            # Removed/expired listings can be served with HTTP 200 (OLX's
            # "inactive ad" page). is_unavailable() is overridden per parser;
            # for OLX it checks the ABSENCE of live-ad data — the i18n phrase
            # "больше не доступно" sits in the JS bundle of every page, so a
            # substring match marks live ads as gone.
            if parser.is_unavailable(html):
                updated.append(_entry(fav, None, False, "объявление больше не доступно", status=status))
                continue
            # Detail pages have a different structure than search card pages.
            # Use extract_detail_price() which each parser overrides with
            # site-specific detail selectors (krisha: jsdata JSON, kn: JSON-LD,
            # etagi: embedded state, olx: [data-testid=ad-price]).
            price, lat, lon = parser.extract_detail_price(html, url)
            if price is None:
                # Page loaded but no price found — the ad changed structure
                # or the price was removed. (Gone ads fail earlier: HTTP error
                # or the OLX "больше не доступно" page.)
                updated.append(_entry(fav, None, False, "цена не найдена на странице", status=status))
                continue
            if price != fav["price"]:
                update_price(fav["listing_key"], price, fav.get("currency", "тг"))
            # Update coordinates if the parser enriched them (e.g. kn.kz
            # fetches the /card/map/{ID} turbo-frame, olx extracts from
            # JSON state). This keeps favorites' map markers accurate.
            if lat is not None and lon is not None:
                update_coords(fav["listing_key"], lat, lon)
            # Refresh the contact phone (often hidden behind a button on
            # the live page, but present in the source).
            try:
                phone = parser.extract_detail_phone(html)
                if phone and phone != (fav.get("phone") or ""):
                    update_phone(fav["listing_key"], phone)
            except Exception:
                pass
            updated.append(_entry(fav, price, price != fav["price"], status=status))
        except Exception as exc:
            updated.append(_entry(fav, None, False, str(exc)[:200]))
        # Be polite: no burst of requests to the same site.
        time.sleep(random.uniform(0.5, 1.5))
    return updated


# ============================================================
# Utility
# ============================================================

def init_db():
    get_conn()


# ============================================================
# Buildings (task_2gis_2do.md §11.1)
# ============================================================

def upsert_building(data: dict) -> dict | None:
    """Insert or update a building record by ``building_id``."""
    bid = str(data.get("building_id") or "").strip()
    if not bid:
        return None
    row = {
        "building_id": bid,
        "address": str(data.get("address") or ""),
        "street": str(data.get("street") or ""),
        "house_number": str(data.get("house_number") or ""),
        "lat": data.get("lat"),
        "lon": data.get("lon"),
        "floors_total": data.get("floors_total"),
        "building_material": str(data.get("building_material") or ""),
        "district": str(data.get("district") or ""),
        "microdistrict": str(data.get("microdistrict") or ""),
        "residential_complex": str(data.get("residential_complex") or ""),
        "year_built": str(data.get("year_built") or ""),
        "updated_at": _now(),
    }
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO buildings (building_id, address, street, house_number, "
            "lat, lon, floors_total, building_material, district, microdistrict, "
            "residential_complex, year_built, updated_at) "
            "VALUES (:building_id, :address, :street, :house_number, :lat, :lon, "
            ":floors_total, :building_material, :district, :microdistrict, "
            ":residential_complex, :year_built, :updated_at) "
            "ON CONFLICT(building_id) DO UPDATE SET "
            "address=excluded.address, street=excluded.street, "
            "house_number=excluded.house_number, lat=excluded.lat, lon=excluded.lon, "
            "floors_total=excluded.floors_total, "
            "building_material=excluded.building_material, "
            "district=excluded.district, microdistrict=excluded.microdistrict, "
            "residential_complex=excluded.residential_complex, "
            "year_built=excluded.year_built, updated_at=excluded.updated_at",
            row,
        )
    return get_building(bid)


def get_building(building_id: str) -> dict | None:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM buildings WHERE building_id = ?",
                    (building_id,))
        r = cur.fetchone()
        return dict(r) if r else None


def list_buildings() -> list[dict]:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM buildings ORDER BY updated_at DESC")
        return [dict(r) for r in cur.fetchall()]


# ============================================================
# Organizations / agencies (task_2gis_2do.md §11.2, §12)
# ============================================================

def upsert_organization(data: dict) -> dict | None:
    org_id = str(data.get("org_id") or "").strip()
    if not org_id:
        return None
    row = {
        "org_id": org_id,
        "name": str(data.get("name") or ""),
        "rubrics": "|".join(data.get("rubrics") or []),
        "rating": data.get("rating"),
        "review_count": data.get("review_count"),
        "address": str(data.get("address") or ""),
        "lat": data.get("lat"),
        "lon": data.get("lon"),
        "website": str(data.get("website") or ""),
        "phones": "|".join(data.get("phones") or []),
        "parent_org_id": str(data.get("parent_org_id") or ""),
        "branch_count": data.get("branch_count"),
        "updated_at": _now(),
    }
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO organizations (org_id, name, rubrics, rating, "
            "review_count, address, lat, lon, website, phones, parent_org_id, "
            "branch_count, updated_at) "
            "VALUES (:org_id, :name, :rubrics, :rating, :review_count, :address, "
            ":lat, :lon, :website, :phones, :parent_org_id, :branch_count, :updated_at) "
            "ON CONFLICT(org_id) DO UPDATE SET name=excluded.name, "
            "rubrics=excluded.rubrics, rating=excluded.rating, "
            "review_count=excluded.review_count, address=excluded.address, "
            "lat=excluded.lat, lon=excluded.lon, website=excluded.website, "
            "phones=excluded.phones, parent_org_id=excluded.parent_org_id, "
            "branch_count=excluded.branch_count, updated_at=excluded.updated_at",
            row,
        )
    return get_organization(org_id)


def get_organization(org_id: str) -> dict | None:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM organizations WHERE org_id = ?", (org_id,))
        r = cur.fetchone()
        return dict(r) if r else None


def list_organizations(q: str = "") -> list[dict]:
    with db_cursor() as cur:
        if q:
            like = f"%{q}%"
            cur.execute(
                "SELECT * FROM organizations WHERE name LIKE ? OR address LIKE ? "
                "OR phones LIKE ? ORDER BY updated_at DESC",
                (like, like, like))
        else:
            cur.execute("SELECT * FROM organizations ORDER BY updated_at DESC")
        return [dict(r) for r in cur.fetchall()]


def close_db():
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


# Service data that must survive a DB reset: wiping the whole file would
# turn every /start into "first user = admin" — and a random user could
# claim the admin role by being the first to /start after a reset.
_SERVICE_TABLES = ("bot_users", "bot_subscription_requests", "bot_support_messages")


def reset_db():
    close_db()
    # Snapshot bot/service rows so they can be restored after the wipe.
    snapshot: dict[str, tuple[list[str], list[tuple]]] = {}
    if DB_PATH.exists():
        try:
            raw = sqlite3.connect(str(DB_PATH))
            try:
                for t in _SERVICE_TABLES:
                    cols = [r[1] for r in raw.execute(f"PRAGMA table_info({t})")]
                    if cols:
                        snapshot[t] = (cols, raw.execute(
                            f"SELECT * FROM {t}").fetchall())
            finally:
                raw.close()
        except Exception:
            snapshot = {}  # unreadable/corrupt file: nothing to preserve
    # WAL mode keeps side-car files; deleting only the main db would leave
    # stale -wal/-shm behind and corrupt the "fresh" database on next open.
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(DB_PATH) + suffix)
        if p.exists():
            p.unlink()
    get_conn()
    # Restore service rows (searches/favorites stay wiped).
    for t, (cols, rows) in snapshot.items():
        if not rows:
            continue
        try:
            with db_cursor() as cur:
                cur.executemany(
                    f"INSERT OR REPLACE INTO {t} ({', '.join(cols)}) "
                    f"VALUES ({', '.join('?' for _ in cols)})", rows)
        except Exception:
            pass  # schema drift / unreadable snapshot: fail open, table empty


# ============================================================
# Telegram bot users (multi-user access; first user = admin)
# ============================================================

def register_bot_user(telegram_id: int, username: str = "",
                      first_name: str = "") -> dict:
    """Register a bot user on /start; the FIRST ever user becomes admin.

    Returns the user row (with 'role'). Existing users just get their
    last_seen refreshed (and username/first_name updated if changed).
    """
    now = _now()
    with db_cursor() as cur:
        cur.execute("SELECT * FROM bot_users WHERE telegram_id = ?", (telegram_id,))
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE bot_users SET username = ?, first_name = ?, last_seen = ? "
                "WHERE telegram_id = ?",
                (username or row["username"], first_name or row["first_name"],
                 now, telegram_id))
            cur.execute("SELECT * FROM bot_users WHERE telegram_id = ?", (telegram_id,))
            return dict(cur.fetchone())
        # First-ever user is promoted to admin (empty table = first).
        cur.execute("SELECT COUNT(*) AS n FROM bot_users")
        is_first = cur.fetchone()["n"] == 0
        role = "admin" if is_first else "user"
        cur.execute(
            "INSERT INTO bot_users "
            "(telegram_id, username, first_name, role, searches, joined_at, last_seen) "
            "VALUES (?, ?, ?, ?, 0, ?, ?)",
            (telegram_id, username, first_name, role, now, now))
    return get_bot_user(telegram_id)


def get_bot_user(telegram_id: int) -> dict | None:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM bot_users WHERE telegram_id = ?", (telegram_id,))
        r = cur.fetchone()
        return dict(r) if r else None


def is_bot_admin(telegram_id: int) -> bool:
    u = get_bot_user(telegram_id)
    return bool(u and u["role"] == "admin")


def list_bot_users() -> list[dict]:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM bot_users ORDER BY joined_at")
        return [dict(r) for r in cur.fetchall()]


def set_bot_user_role(telegram_id: int, role: str) -> bool:
    if role not in ("admin", "user"):
        raise ValueError("role must be 'admin' or 'user'")
    with db_cursor() as cur:
        cur.execute("UPDATE bot_users SET role = ? WHERE telegram_id = ?",
                    (role, telegram_id))
        return cur.rowcount > 0


def bump_bot_user_searches(telegram_id: int) -> None:
    """Count one search: lifetime total + today's counter (day-rollover aware).

    ``searches_today`` resets automatically when ``last_search_day`` differs
    from today — no cron job needed.
    """
    today = _today()
    with db_cursor() as cur:
        cur.execute(
            "SELECT last_search_day FROM bot_users WHERE telegram_id = ?",
            (telegram_id,))
        row = cur.fetchone()
        if row is None:
            return
        if row["last_search_day"] == today:
            cur.execute(
                "UPDATE bot_users SET searches_today = searches_today + 1, "
                "searches = searches + 1, last_seen = ? WHERE telegram_id = ?",
                (_now(), telegram_id))
        else:
            cur.execute(
                "UPDATE bot_users SET searches_today = 1, last_search_day = ?, "
                "searches = searches + 1, last_seen = ? WHERE telegram_id = ?",
                (today, _now(), telegram_id))


def count_bot_searches_today(telegram_id: int) -> int:
    """Searches this user made today (0 before the first one today)."""
    u = get_bot_user(telegram_id)
    if not u or u.get("last_search_day") != _today():
        return 0
    return int(u.get("searches_today") or 0)


# --- Support inbox: users write, admins answer ---------------------------

def add_support_message(telegram_id: int, username: str, text: str) -> int:
    """Store a support request. Returns its id."""
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO bot_support_messages (telegram_id, username, text, created_at) "
            "VALUES (?, ?, ?, ?)", (telegram_id, username, text, _now()))
        return cur.lastrowid


def list_support_messages(limit: int = 50, only_open: bool = False) -> list[dict]:
    q = ("SELECT * FROM bot_support_messages"
         + (" WHERE answered = 0" if only_open else "")
         + " ORDER BY id DESC LIMIT ?")
    with db_cursor() as cur:
        cur.execute(q, (limit,))
        return [dict(r) for r in cur.fetchall()]


def mark_support_answered(msg_id: int) -> bool:
    with db_cursor() as cur:
        cur.execute("UPDATE bot_support_messages SET answered = 1 WHERE id = ?",
                    (msg_id,))
        return cur.rowcount > 0


# --- Subscription stub: collect interested users until billing exists ---

def add_subscription_request(telegram_id: int, username: str = "") -> int:
    """Record a paid-subscription interest. Returns the queue position (FIFO).

    Position is fixed by the FIRST request time: re-requesting updates the
    username but keeps the original place in line (rows with the same
    timestamp — same second — are ordered by telegram id, deterministic).
    """
    now = _now()
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO bot_subscription_requests (telegram_id, username, requested_at) "
            "VALUES (?, ?, ?) ON CONFLICT(telegram_id) DO UPDATE SET "
            "username = excluded.username",
            (telegram_id, username, now))
        cur.execute(
            "SELECT COUNT(*) AS n FROM bot_subscription_requests me "
            "JOIN bot_subscription_requests other "
            "ON other.telegram_id != me.telegram_id "
            "AND (other.requested_at < me.requested_at "
            "     OR (other.requested_at = me.requested_at "
            "         AND other.telegram_id < me.telegram_id)) "
            "WHERE me.telegram_id = ?", (telegram_id,))
        return cur.fetchone()["n"] + 1


def list_subscription_requests() -> list[dict]:
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM bot_subscription_requests ORDER BY requested_at")
        return [dict(r) for r in cur.fetchall()]
