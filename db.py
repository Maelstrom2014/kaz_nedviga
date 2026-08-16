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
"""

# Columns added after the initial schema. Applied on connect so older
# database files created before these fields existed are upgraded in place.
_MIGRATIONS = [
    ("favorites", "date_published", "TEXT NOT NULL DEFAULT ''"),
    ("favorites", "date_updated", "TEXT NOT NULL DEFAULT ''"),
    ("favorites", "lat", "REAL"),
    ("favorites", "lon", "REAL"),
]


def _apply_migrations(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(favorites)")}
    for table, col, decl in _MIGRATIONS:
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            cols.add(col)


def listing_key(url: str, source: str, title: str = "") -> str:
    raw = f"{source}|{url}|{title}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


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
    """Wipe all data from both tables but keep the schema (no file delete)."""
    with db_cursor() as cur:
        cur.execute("DELETE FROM price_history")
        cur.execute("DELETE FROM favorites")


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

    def _entry(fav, new_price, changed, error=None):
        item = {
            "listing_key": fav["listing_key"],
            "title": fav["title"],
            "old_price": fav["price"],
            "new_price": new_price,
            "changed": changed,
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
            # OLX returns 200 with "Объявление больше не доступно" for
            # removed/expired listings. Early-exit before parsing so the
            # favorite is marked unavailable immediately and clearly.
            if source == "olx.kz" and "больше не доступно" in html.lower():
                updated.append(_entry(fav, None, False, "объявление больше не доступно"))
                continue
            # Detail pages have a different structure than search card pages.
            # Use extract_detail_price() which each parser overrides with
            # site-specific detail selectors (krisha: jsdata JSON, kn: JSON-LD,
            # etagi: embedded state, olx: [data-testid=ad-price]).
            price, lat, lon = parser.extract_detail_price(html, url)
            if price is None:
                updated.append(_entry(fav, None, False, "объявление не найдено на странице"))
                continue
            if price != fav["price"]:
                update_price(fav["listing_key"], price, fav.get("currency", "тг"))
            # Update coordinates if the parser enriched them (e.g. kn.kz
            # fetches the /card/map/{ID} turbo-frame, olx extracts from
            # JSON state). This keeps favorites' map markers accurate.
            if lat is not None and lon is not None:
                update_coords(fav["listing_key"], lat, lon)
            updated.append(_entry(fav, price, price != fav["price"]))
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


def close_db():
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def reset_db():
    close_db()
    # WAL mode keeps side-car files; deleting only the main db would leave
    # stale -wal/-shm behind and corrupt the "fresh" database on next open.
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(DB_PATH) + suffix)
        if p.exists():
            p.unlink()
    get_conn()
