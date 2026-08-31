"""Shared state + helpers for the web app.

Every Flask blueprint module references shared dependencies (parsers, proxy
pool, results cache, settings) through this module — ``core.load_settings()``,
``core._RESULTS_CACHE`` — so tests can monkeypatch a single location
(``webapp.core.*``) and have all routes pick it up.
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import socket
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from flask import request

# --- Patchable dependencies (routes call these via `core.<name>`) ---
from geo.districts import get_districts_json, get_district_list  # noqa: F401
from parsers.factory import get_all_parsers, list_sites  # noqa: F401
from parsers.base import get_all_parser_stats, reset_parser_stats  # noqa: F401
from parsers.models import Listing, SearchParams
from parsers.proxy import (
    get_pool as get_proxy_pool,
    DEFAULT_PROXY_SETTINGS,
    PROXY_SOURCES,
    sanitize_proxy_settings,
    KNOWN_SITES,
)
import rates as rates_module  # noqa: F401
from export_utils import export_txt, export_pdf  # noqa: F401
from db import (  # noqa: F401
    add_favorite as db_add_favorite,
    get_favorite as db_get_favorite,
    get_favorite_by_id,
    list_favorites as db_list_favorites,
    clear_favorites as db_clear_favorites,
    clear_all as db_clear_all,
    update_rating as db_update_rating,
    update_comment as db_update_comment,
    update_price as db_update_price,
    delete_favorite as db_delete_favorite,
    get_price_history as db_get_price_history,
    check_prices as db_check_prices,
    listing_key,
    init_db,
    list_organizations as db_list_organizations,
    list_buildings as db_list_buildings,
    reset_db,
)

log = logging.getLogger("app")

# ============================================================
# Settings (persisted across server restarts)
# ============================================================
SETTINGS_PATH = Path(__file__).parent.parent / "data" / "settings.json"
THEMES = [
    "midnight", "carbon", "forest",            # dark
    "daylight", "sand", "rose",                # light
]
_DEFAULT_THEME = "midnight"
_DEFAULT_SEARCH_DEFAULTS = {
    "price_min": 100000,
    "price_max": 300000,
    "rooms": [0, 1],          # 0 = студия, 1 = однокомнатные
    "district": "",
    "floor_min": None,
    "floor_max": None,
    "area_min": None,
    "area_max": None,
}
_DEFAULT_SETTINGS = {"theme": _DEFAULT_THEME,
                     "hide_no_photo": False,
                     "search_defaults": _DEFAULT_SEARCH_DEFAULTS,
                      "photo_cache_mb": 500,
                      "olx_phone_page_only": False,
                      "olx_phone_playwright": False,
                     # Background scheduler: refresh cached listings every N hours.
                     # Off by default — explicit opt-in (parser.network load).
                     "scheduler_enabled": False,
                     "parser_interval_hours": 4,
                     "parser_max_pages": {
                         "krisha.kz": 9,
                         "olx.kz": 6,
                         "kn.kz": 6,
                         "etagi.com": 3,
                         "telegram": 3,
                          "twogis": 3,
                      },
                      # Free proxies: fetch from public lists, test them, rotate across parsers.
                      "proxy": DEFAULT_PROXY_SETTINGS,
                  }

_SEARCH_DEFAULTS_KEYS = {
    "price_min", "price_max", "rooms", "district",
    "floor_min", "floor_max", "area_min", "area_max",
}

# Sane bounds for per-parser page counts (the form clamps to this range).
_MAX_PAGES_MIN = 1
_MAX_PAGES_MAX = 30

# Sane bounds for the photo cache size (MB).
_PHOTO_CACHE_MB_MIN = 50
_PHOTO_CACHE_MB_MAX = 10000

# Sane bounds for the background scheduler interval (hours).
_SCHEDULER_INTERVAL_MIN = 1
_SCHEDULER_INTERVAL_MAX = 168


def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return dict(_DEFAULT_SETTINGS)
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return dict(_DEFAULT_SETTINGS)
    if not isinstance(data, dict) or data.get("theme") not in THEMES:
        return dict(_DEFAULT_SETTINGS)
    # Backfill search_defaults if missing or partial
    sd = data.get("search_defaults")
    if not isinstance(sd, dict):
        data["search_defaults"] = dict(_DEFAULT_SEARCH_DEFAULTS)
    else:
        base = dict(_DEFAULT_SEARCH_DEFAULTS)
        base.update({k: v for k, v in sd.items() if k in _SEARCH_DEFAULTS_KEYS})
        data["search_defaults"] = base
    # Backfill parser_max_pages (per-parser page-count overrides).
    pmp = data.get("parser_max_pages")
    if not isinstance(pmp, dict):
        data["parser_max_pages"] = dict(_DEFAULT_SETTINGS["parser_max_pages"])
    else:
        base = dict(_DEFAULT_SETTINGS["parser_max_pages"])
        for name, val in pmp.items():
            if isinstance(val, int) and _MAX_PAGES_MIN <= val <= _MAX_PAGES_MAX:
                base[name] = val
        data["parser_max_pages"] = base
    # Backfill photo_cache_mb (max on-disk photo cache size in MB).
    pcm = data.get("photo_cache_mb")
    if not isinstance(pcm, int) or not _PHOTO_CACHE_MB_MIN <= pcm <= _PHOTO_CACHE_MB_MAX:
        data["photo_cache_mb"] = _DEFAULT_SETTINGS["photo_cache_mb"]
    # Backfill scheduler settings: scheduler_enabled (bool), parser_interval_hours.
    se = data.get("scheduler_enabled")
    if not isinstance(se, bool):
        data["scheduler_enabled"] = False
    pih = data.get("parser_interval_hours")
    if not isinstance(pih, int) or not _SCHEDULER_INTERVAL_MIN <= pih <= _SCHEDULER_INTERVAL_MAX:
        data["parser_interval_hours"] = _DEFAULT_SETTINGS["parser_interval_hours"]
    # Backfill the twogis parser_max_pages key (added in task_2gis_2do.md).
    pmp = data.get("parser_max_pages")
    if isinstance(pmp, dict) and "twogis" not in pmp:
        pmp["twogis"] = _DEFAULT_SETTINGS["parser_max_pages"]["twogis"]
        data["parser_max_pages"] = pmp
    # Backfill proxy settings (free-proxy pipeline); coerce to clean values.
    data["proxy"] = sanitize_proxy_settings(data.get("proxy"))

    return data


def save_settings(data: dict):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _to_dict(item: Listing) -> dict:
    d = asdict(item)
    d["price_str"] = item.price_str()
    d["price_rub"] = item.price_rub()
    d["price_usd"] = item.price_usd()
    d["price_all_str"] = item.price_all_str()
    # Stable server-side key (same algorithm as db.listing_key) so the
    # front-end can address favorites without re-implementing the hash.
    d["listing_key"] = listing_key(item.url, item.source, item.title)
    return d


def _parse_params(args) -> SearchParams:
    def _int(val) -> Optional[int]:
        if val is None:
            return None
        if isinstance(val, (int, float)):
            return int(val)
        try:
            s = str(val).strip()
            return int(s) if s else None
        except (ValueError, TypeError):
            return None

    def _str(val) -> str:
        if val is None:
            return ""
        return str(val).strip()

    def _get(args, key, default=""):
        if hasattr(args, "getlist"):
            return args.get(key, default)
        return args.get(key, default)

    rooms = []
    if hasattr(args, "getlist"):
        rooms_raw = args.getlist("rooms")
    else:
        rooms_val = args.get("rooms", [])
        if isinstance(rooms_val, list):
            rooms_raw = rooms_val
        elif rooms_val:
            rooms_raw = [rooms_val]
        else:
            rooms_raw = []
    # 0 = studio, 1..6 rooms; anything else is client noise and would
    # either break URL filters (negatives) or match nothing (7+).
    for r in rooms_raw:
        n = _int(r)
        if n is not None and 0 <= n <= 6:
            rooms.append(n)
    if not rooms:
        n = _int(_get(args, "rooms"))
        if n is not None and 0 <= n <= 6:
            rooms = [n]

    limit_raw = _int(args.get("limit"))
    limit = limit_raw if limit_raw is not None else 20

    # Pages per site: 0/missing = site default, clamp to a sane 1..10
    max_pages_raw = _int(args.get("max_pages"))
    max_pages = 0
    if max_pages_raw is not None and max_pages_raw > 0:
        max_pages = min(max_pages_raw, 10)

    return SearchParams(
        query=_str(args.get("query", "")),
        city="almaty",
        rooms=rooms,
        price_min=_int(args.get("price_min")),
        price_max=_int(args.get("price_max")),
        district=_str(args.get("district", "")),
        floor_min=_int(args.get("floor_min")),
        floor_max=_int(args.get("floor_max")),
        area_min=_int(args.get("area_min")),
        area_max=_int(args.get("area_max")),
        limit=limit,
        max_pages=max_pages,
    )


def _apply_parser_max_pages(parsers: list) -> None:
    """Apply per-parser page-count overrides from settings in place.

    Called before ``run()`` so each parser fetches the configured number of
    pages. The form's global ``max_pages`` (``params.max_pages > 0``) still
    wins — it is applied uniformly inside ``BaseParser.run()``.
    """
    settings = load_settings()
    pmp = settings.get("parser_max_pages", {})
    page_only = settings.get("olx_phone_page_only", False)
    playwright = settings.get("olx_phone_playwright", False)
    for p in parsers:
        if p.name in pmp:
            try:
                p.max_pages = int(pmp[p.name])
            except (TypeError, ValueError):
                pass
        if page_only and p.name == "olx.kz":
            p.phone_endpoint_enabled = False
        if playwright and p.name == "olx.kz":
            p.phone_playwright_enabled = True


# --- Previous results cache (JSON file) ---
_RESULTS_CACHE = Path(__file__).parent.parent / "data" / "last_results.json"


def _run_all_parsers(parsers: list, params: SearchParams) -> list[Listing]:
    """Run ``parsers`` concurrently against ``params`` and return flat results.

    Shared between the on-demand ``/api/search`` path and the background
    ``scheduler`` (so both paths behave identically: same workers, same
    parser-error handling, same logging).
    """
    results: list[Listing] = []
    log.info("[search] dispatching %d parsers%s", len(parsers),
             f" ({', '.join(p.name for p in parsers)})" if parsers else "")
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(p.run, params): p.name for p in parsers}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                items = fut.result()
                results.extend(items)
                log.info("[search] parser %s returned %d results", name, len(items))
            except Exception as exc:
                log.warning("[search] parser %s failed: %s", name, exc, exc_info=True)
    log.info("[search] total %d listings from %d parsers", len(results), len(parsers))
    return results


def _is_apartment_listing(r: dict) -> bool:
    """True when a cached entry carries at least one rental signal
    (price, rooms or area). Entries without any of them — e.g. "free
    give-away" items (kittens, TVs) left in the cache by an old build's
    extended-search fallback — are junk and must not be re-served."""
    return (r.get("price") is not None or r.get("rooms") is not None
            or r.get("area") is not None)


def _load_prev_results() -> dict[str, dict]:
    """Load previous search results keyed by URL+source."""
    if not _RESULTS_CACHE.exists():
        return {}
    try:
        data = json.loads(_RESULTS_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    # Drop junk entries (no price/rooms/area) so stale poison from an old
    # build can never be re-served or re-saved by the merge below.
    dropped = 0
    for key in [k for k, r in data.items()
                if not (isinstance(r, dict) and _is_apartment_listing(r))]:
        title = data[key].get("title", "") if isinstance(data[key], dict) else ""
        log.warning("Dropped junk cache entry (%s): %s", key, title)
        del data[key]
        dropped += 1
    if dropped:
        log.info("Cache sanitized: %d junk entr%s dropped",
                 dropped, "y" if dropped == 1 else "ies")
    # Backfill stable keys for entries cached before listing_key was stored.
    for r in data.values():
        if isinstance(r, dict) and not r.get("listing_key"):
            r["listing_key"] = listing_key(
                r.get("url", ""), r.get("source", ""), r.get("title", "")
            )
    return data


def _save_results(results: list[dict]):
    """Save results to cache file, merging with old data."""
    _RESULTS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    prev = _load_prev_results()
    now_iso = datetime.datetime.now().isoformat(sep=" ", timespec="seconds")
    for r in results:
        key = f"{r.get('source','')}|{r.get('url','')}"
        old = prev.get(key)
        # Persist first-seen (date added/parsed); backfill pre-existing rows
        r["first_seen"] = old.get("first_seen") if old and old.get("first_seen") else now_iso
        if old:
            # Track price change
            old_price = old.get("price")
            new_price = r.get("price")
            if new_price is not None and old_price is not None and new_price != old_price:
                r["price_changed"] = True
                r["prev_price"] = old_price
            # Keep old rating/comment if present
            if old.get("rating"):
                r["prev_rating"] = old["rating"]
        prev[key] = r
    _RESULTS_CACHE.write_text(json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf-8")
    return prev


def _write_results_cache(data: dict) -> None:
    """Persist the merged results dict to the cache file."""
    _RESULTS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _RESULTS_CACHE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# How many cached (not re-fetched this run) cards to re-check for validity per
# search. Bounds the added latency; the rest are picked up on later runs.
MAX_CACHE_CHECKS_PER_RUN = 30


def _check_cached_cards(cards: list[dict], parsers: dict,
                        limit: int = MAX_CACHE_CHECKS_PER_RUN,
                        max_age_hours: float = 6) -> int:
    """Re-check a bounded subset of cached cards for validity.

    Mirrors the favorites price-check: fetch each URL, read the on-page status
    and whether the ad is gone. Mutates the card dicts in place
    (``check_status`` / ``unavailable`` / ``checked_at``). Cards checked within
    ``max_age_hours`` are skipped so the work spreads over runs. Returns the
    number of cards checked this run.
    """
    import time

    now = datetime.datetime.now()
    max_age = max_age_hours * 3600
    candidates = []
    for r in cards:
        if not r.get("url") or r.get("source") not in parsers:
            continue
        checked_at = r.get("checked_at")
        if checked_at:
            try:
                ca = datetime.datetime.fromisoformat(checked_at)
                if (now - ca).total_seconds() < max_age:
                    continue  # checked recently — leave it for a later run
            except Exception:
                pass
        candidates.append(r)
    # Never-checked first, then least-recently-checked, then a stable tie-break.
    candidates.sort(
        key=lambda r: (r.get("checked_at") or "", r.get("source", ""), r.get("url", ""))
    )
    to_check = candidates[:limit]
    if not to_check:
        return 0

    def _check_one(r):
        parser = parsers[r["source"]]
        try:
            html = parser.fetch(r["url"])
            r["unavailable"] = bool(parser.is_unavailable(html))
            status = (parser.detect_status(html) or "").strip()
            if status:
                r["check_status"] = status
            else:
                r.pop("check_status", None)
        except Exception:
            # Fetch failed (404 / network): the ad can't be verified as live.
            r["unavailable"] = True
            r["check_status"] = "не удалось проверить"
        r["checked_at"] = now.isoformat(sep=" ", timespec="seconds")
        time.sleep(0.2)  # be gentle to the source
        return 1

    # Fetch in a small pool so a bounded subset takes only a few seconds even
    # without a proxy, while still spreading requests to each source.
    with ThreadPoolExecutor(max_workers=3) as ex:
        return sum(ex.map(_check_one, to_check))


# One-shot signals computed for a specific search run. They stay in the cache
# file (tests rely on that) but must not be served back as "current" badges —
# on the next run or a page reload they would be stale forever.
_VOLATILE_FLAGS = ("is_new", "price_changed", "prev_price")


def _strip_volatile_flags(results: list[dict]) -> list[dict]:
    for r in results:
        for k in _VOLATILE_FLAGS:
            r.pop(k, None)
    return results


def _filter_no_photo(results: list[dict]) -> list[dict]:
    """Serve only listings that have a photo, when the user enabled the
    'hide no-photo listings' setting. The cache file itself is untouched —
    the filter is applied at response time only, so the setting is fully
    reversible (turn it off and photo-less listings reappear)."""
    if not load_settings().get("hide_no_photo"):
        return results
    return [r for r in results if (r.get("photo") or "").strip()]


def _point_in_polygon(lat: float, lon: float, polygon: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon test.

    ``polygon`` is a list of ``[lat, lon]`` pairs. Returns True if the
    point is inside the polygon (used to locate a listing's district by
    its coordinates — more reliable than substring matching).
    """
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i][0], polygon[i][1]
        yj, xj = polygon[j][0], polygon[j][1]
        # intersection of the ray with the polygon edge
        if (yi > lat) != (yj > lat):
            x_int = (xj - xi) * (lat - yi) / (yj - yi + 1e-12) + xi
            if lon < x_int:
                inside = not inside
        j = i
    return inside


def _dict(params: SearchParams) -> dict:
    d = asdict(params)
    return {k: v for k, v in d.items()}


def _build_listings_from_request() -> list[Listing]:
    args = request.get_json(silent=True) or request.form
    raw_results = args.get("results", [])
    items: list[Listing] = []
    for r in raw_results:
        items.append(Listing(
            title=r.get("title", ""),
            price=r.get("price"),
            currency=r.get("currency", "тг"),
            rooms=r.get("rooms"),
            area=r.get("area"),
            floor=r.get("floor"),
            total_floors=r.get("total_floors"),
            address=r.get("address", ""),
            url=r.get("url", ""),
            source=r.get("source", ""),
            date_published=r.get("date_published", ""),
            date_updated=r.get("date_updated", ""),
            photo=r.get("photo", ""),
        ))
    return items


def _compute_price_change(history: list[dict]) -> dict | None:
    if len(history) < 2:
        return None
    prices = [h["price"] for h in history if h.get("price") is not None]
    if len(prices) < 2:
        return None
    old = prices[0]
    new = prices[-1]
    diff = new - old
    pct = round(diff / old * 100, 1) if old else 0
    return {
        "old_price": old,
        "new_price": new,
        "diff": diff,
        "percent": pct,
        "direction": "up" if diff > 0 else ("down" if diff < 0 else "stable"),
    }


def _port_is_free(port: int) -> bool:
    """True when nothing listens on *port*.

    Uses SO_EXCLUSIVEADDRUSE so the probe also detects listeners that were
    bound with SO_REUSEADDR (which is what Werkzeug's dev server does).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        sock.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _port_owner_pids(port: int) -> str:
    """PIDs listening on *port* (via ``netstat -ano``), or '' if unknown."""
    try:
        # errors="replace": netstat output is locale-encoded (cp1251 here);
        # a hard decode failure would leave stdout=None in the reader thread.
        out = subprocess.run(["netstat", "-ano"], capture_output=True,
                             text=True, errors="replace",
                             timeout=10).stdout or ""
    except Exception:
        return ""
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" \
                and parts[1].rsplit(":", 1)[-1] == str(port):
            pids.add(parts[4])
    return ", ".join(sorted(pids))
