from __future__ import annotations

import datetime
import logging
import json
import re
import socket
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, render_template, request, send_file, Response

from data.districts import get_districts_json, get_district_list
from parsers.factory import get_all_parsers, list_sites
from parsers.base import get_all_parser_stats, reset_parser_stats
from parsers.models import Listing, SearchParams
from parsers.proxy import (
    get_pool as get_proxy_pool,
    DEFAULT_PROXY_SETTINGS,
    PROXY_SOURCES,
    sanitize_proxy_settings,
    KNOWN_SITES,
)
import rates as rates_module
from export_utils import export_txt, export_pdf
from db import (
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

# --- Logging setup: file + console ---
_log_path = Path(__file__).parent / "parsers_errors.log"
_log_fmt = logging.Formatter(
    "%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
# mode="a": both this logger and the "parsers" logger write to the same file;
# "w" truncated the log on every import/restart. File keeps errors only;
# the console gets the full INFO stream so search activity is visible.
_fh = logging.FileHandler(str(_log_path), mode="a", encoding="utf-8")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(_log_fmt)
_ch = logging.StreamHandler()
_ch.setLevel(logging.DEBUG)
_ch.setFormatter(_log_fmt)
log = logging.getLogger("app")
log.setLevel(logging.DEBUG)
if not log.handlers:
    log.addHandler(_fh)
    log.addHandler(_ch)
# Console output must not depend on werkzeug's root handler (which is not
# always present, e.g. under `flask run`); also prevents double printing
# when the dev server does add one.
log.propagate = False
init_db()

# Pre-warm exchange rates in the background: the first /api/rates (and the
# first RUB/USD conversion) must not pay for a network fetch. If the file
# cache is fresh this returns instantly; otherwise the fetch runs while the
# server is coming up.
threading.Thread(target=rates_module.get_rates, daemon=True, name="rates-prewarm").start()

# The background scheduler is started at the bottom of this module (after
# load_settings is defined) — see "Start the background scheduler" near
# the if __name__ == "__main__" block.

app = Flask(__name__, template_folder="templates", static_folder="static")
# JSON_AS_ASCII was removed in Flask 2.3; ensure_ascii on the JSON provider is
# the supported way to keep Cyrillic readable in API responses.
app.json.ensure_ascii = False

# ============================================================
# Settings (persisted across server restarts)
# ============================================================
SETTINGS_PATH = Path(__file__).parent / "data" / "settings.json"
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


@app.route("/")
def index():
    return render_template(
        "index.html",
        sites=list_sites(),
        districts=get_district_list(),
        themes=THEMES,
        theme=load_settings().get("theme", _DEFAULT_THEME),
    )


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    data = load_settings()
    # Normalize so clients always see every known key
    data.setdefault("hide_no_photo", False)
    data.setdefault("photo_cache_mb", _DEFAULT_SETTINGS["photo_cache_mb"])
    data.setdefault("scheduler_enabled", False)
    data.setdefault("parser_interval_hours", _DEFAULT_SETTINGS["parser_interval_hours"])
    data.setdefault("proxy", DEFAULT_PROXY_SETTINGS)
    get_proxy_pool().configure(data.get("proxy"))
    return jsonify(data)


@app.route("/api/settings", methods=["POST"])
def api_set_settings():
    args = request.get_json(silent=True) or request.form
    data = load_settings()
    if "theme" in args:
        theme = args.get("theme")
        if theme not in THEMES:
            return jsonify({"error": f"Unknown theme. Available: {THEMES}"}), 400
        data["theme"] = theme
    if "hide_no_photo" in args:
        data["hide_no_photo"] = bool(args["hide_no_photo"])
    if "olx_phone_page_only" in args:
        data["olx_phone_page_only"] = bool(args["olx_phone_page_only"])
    if "olx_phone_playwright" in args:
        data["olx_phone_playwright"] = bool(args["olx_phone_playwright"])
    if "photo_cache_mb" in args:
        try:
            pcm = int(args["photo_cache_mb"])
        except (TypeError, ValueError):
            pcm = _DEFAULT_SETTINGS["photo_cache_mb"]
        data["photo_cache_mb"] = max(_PHOTO_CACHE_MB_MIN,
                                     min(_PHOTO_CACHE_MB_MAX, pcm))
    if "scheduler_enabled" in args:
        data["scheduler_enabled"] = bool(args["scheduler_enabled"])
    if "parser_interval_hours" in args:
        try:
            pih = int(args["parser_interval_hours"])
        except (TypeError, ValueError):
            pih = _DEFAULT_SETTINGS["parser_interval_hours"]
        data["parser_interval_hours"] = max(_SCHEDULER_INTERVAL_MIN,
                                            min(_SCHEDULER_INTERVAL_MAX, pih))
    if "parser_max_pages" in args:
        pmp = args.get("parser_max_pages")
        if not isinstance(pmp, dict):
            return jsonify({"error": "parser_max_pages must be an object"}), 400
        base = dict(_DEFAULT_SETTINGS["parser_max_pages"])
        for name, val in pmp.items():
            try:
                n = int(val)
            except (TypeError, ValueError):
                continue
            if _MAX_PAGES_MIN <= n <= _MAX_PAGES_MAX:
                base[name] = n
        data["parser_max_pages"] = base
    if "proxy" in args:
        data["proxy"] = sanitize_proxy_settings(args.get("proxy"))
    save_settings(data)
    # Apply proxy config to the shared pool immediately.
    get_proxy_pool().configure(data.get("proxy"))
    # Apply scheduler config changes to the running daemon immediately.
    try:
        import scheduler
        scheduler.configure(
            enabled=data.get("scheduler_enabled", False),
            interval_hours=data.get("parser_interval_hours",
                                    _DEFAULT_SETTINGS["parser_interval_hours"]),
        )
    except Exception as exc:
        log.warning("[settings] scheduler reconfigure failed: %s", exc)
    return jsonify({"ok": True,
                    "theme": data.get("theme", _DEFAULT_THEME),
                    "hide_no_photo": bool(data.get("hide_no_photo", False)),
                    "photo_cache_mb": data.get("photo_cache_mb", _DEFAULT_SETTINGS["photo_cache_mb"]),
                    "scheduler_enabled": bool(data.get("scheduler_enabled", False)),
                    "parser_interval_hours": data.get("parser_interval_hours",
                                                     _DEFAULT_SETTINGS["parser_interval_hours"]),
                    "parser_max_pages": data.get("parser_max_pages", {})})


@app.route("/api/proxy", methods=["GET"])
def api_proxy_status():
    """Proxy pool config + status (count, last refresh, per-source stats)."""
    data = load_settings()
    pool = get_proxy_pool()
    pool.configure(data.get("proxy"))
    st = pool.status()
    st["config"] = sanitize_proxy_settings(data.get("proxy"))
    st["sources"] = PROXY_SOURCES
    st["known_sites"] = list(KNOWN_SITES)
    st["proxies"] = [e.to_dict() for e in pool.snapshot()]
    st["errors"] = pool.errors(100)
    st["usage"] = pool.usage()
    return jsonify(st)


@app.route("/api/proxy", methods=["POST"])
def api_proxy_update():
    """Update proxy settings (full or partial) and sync the shared pool."""
    args = request.get_json(silent=True) or request.form
    data = load_settings()
    if "proxy" in args:
        data["proxy"] = sanitize_proxy_settings(args.get("proxy"))
    elif args:
        merged = dict(data.get("proxy") or {})
        merged.update(args)
        data["proxy"] = sanitize_proxy_settings(merged)
    save_settings(data)
    get_proxy_pool().configure(data.get("proxy"))
    return jsonify({"ok": True, "config": data["proxy"],
                    "status": get_proxy_pool().status()})


@app.route("/api/proxy/refresh", methods=["POST"])
def api_proxy_refresh():
    """Fetch proxy lists from the enabled sources and test which ones work."""
    args = request.get_json(silent=True) or request.form
    data = load_settings()
    if args and "proxy" in args:
        data["proxy"] = sanitize_proxy_settings(args.get("proxy"))
    cfg = sanitize_proxy_settings(data.get("proxy"))
    pool = get_proxy_pool()
    try:
        stats = pool.refresh(cfg)
    except Exception as exc:
        log.warning("[proxy] refresh failed: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500
    save_settings(data)
    return jsonify({"ok": True, "stats": stats, "status": pool.status()})


@app.route("/api/proxy/clear", methods=["POST"])
def api_proxy_clear():
    get_proxy_pool().clear()
    return jsonify({"ok": True, "status": get_proxy_pool().status()})


@app.route("/api/proxy/errors/clear", methods=["POST"])
def api_proxy_clear_errors():
    get_proxy_pool().clear_errors()
    return jsonify({"ok": True, "error_count": get_proxy_pool().status()["error_count"]})


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


@app.route("/api/search-defaults", methods=["GET"])
def api_get_search_defaults():
    return jsonify(load_settings().get("search_defaults", _DEFAULT_SEARCH_DEFAULTS))


@app.route("/api/search-defaults", methods=["POST"])
def api_set_search_defaults():
    """Persist default search parameters (price_min/max, rooms, district, etc.).

    All fields are replaced atomically: if a field is missing or null it is
    cleared. This keeps the semantics simple and predictable — the front-end
    always submits the full form.
    """
    args = request.get_json(silent=True) or request.form
    data = load_settings()
    sd = {}
    # numeric fields
    for key in ("price_min", "price_max", "floor_min", "floor_max",
                "area_min", "area_max"):
        v = args.get(key)
        if v is None or v == "":
            sd[key] = None
        else:
            try:
                sd[key] = int(v)
            except (TypeError, ValueError):
                sd[key] = None
    # district
    district = args.get("district")
    sd["district"] = str(district).strip() if district else ""
    # rooms (list of ints)
    rooms = args.get("rooms")
    if rooms is None:
        sd["rooms"] = []
    else:
        if isinstance(rooms, str):
            rooms = [rooms]
        sd["rooms"] = [int(r) for r in rooms if str(r).isdigit() or r == 0]
    data["search_defaults"] = sd
    save_settings(data)
    return jsonify({"ok": True, "search_defaults": sd})


@app.route("/api/database/reset", methods=["POST"])
def api_reset_database():
    """Delete the entire database file (favorites + price history) and clear cached results."""
    reset_db()
    if _RESULTS_CACHE.exists():
        _RESULTS_CACHE.unlink()
    return jsonify({"ok": True, "message": "База данных и кэш результатов удалены"})


@app.route("/api/favorites/clear", methods=["POST"])
def api_clear_favorites():
    """Delete all favorites but keep the database file and schema."""
    removed = db_clear_favorites()
    if _RESULTS_CACHE.exists():
        _RESULTS_CACHE.unlink()
    data = load_settings()
    return jsonify({"ok": True, "removed": removed, "theme": data.get("theme", _DEFAULT_THEME)})


# --- Previous results cache (JSON file) ---
_RESULTS_CACHE = Path(__file__).parent / "data" / "last_results.json"


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


@app.route("/api/search", methods=["POST"])
def api_search():
    args = request.get_json(silent=True) or request.form
    params = _parse_params(args)
    selected_sources = args.get("sources", [])
    parsers = get_all_parsers()
    _apply_parser_max_pages(parsers)
    if selected_sources:
        parsers = [p for p in parsers if p.name in selected_sources]

    get_proxy_pool().clear_usage()
    results: list[Listing] = _run_all_parsers(parsers, params)
    results.sort(key=lambda x: (x.price is None, x.price or 0))

    # Building/geo enrichment (task_2gis_2do.md §1.2, §11): fill missing
    # residential_complex / microdistrict / building coords from cached
    # building records + district polygons. Best-effort, never blocks search.
    try:
        from data.buildings import enrich_listings
        enrich_listings(results)
    except Exception as exc:
        log.debug("[search] building enrichment skipped: %s", exc)

    # Merge with previous results and save
    result_dicts = [_to_dict(r) for r in results]
    prev = _load_prev_results()
    # Mark new listings
    for r in result_dicts:
        key = f"{r.get('source','')}|{r.get('url','')}"
        old = prev.get(key)
        if old:
            if r.get("price") is not None and old.get("price") is not None:
                if r["price"] != old["price"]:
                    r["price_changed"] = True
                    r["prev_price"] = old["price"]
            r["is_new"] = False
        elif r.get("url"):
            r["is_new"] = True
    # Save current results (merge: keep old ones too)
    merged = _save_results(result_dicts)

    # Return the MERGED set (previous + current) so previously found
    # apartments don't disappear from the UI on the next search.
    current_keys = {f"{r.get('source','')}|{r.get('url','')}" for r in result_dicts}
    merged_list = []
    cached_cards = []
    for key, r in merged.items():
        if key not in current_keys:
            # Not re-fetched in this run: drop one-shot badges from the
            # previous run, otherwise "NEW"/"CHANGED" would stick forever.
            _strip_volatile_flags([r])
            cached_cards.append(r)
        merged_list.append(r)
    # Re-check a bounded subset of cached cards for validity (removed/expired
    # ads) and mark them on the card — the same info the favorites show.
    # Persist the results so the badges survive a reload.
    if cached_cards:
        _check_cached_cards(cached_cards, {p.name: p for p in get_all_parsers()})
        _write_results_cache(merged)
    merged_list.sort(key=lambda r: (r.get("price") is None, r.get("price") or 0))
    merged_list = _filter_no_photo(merged_list)

    _pool = get_proxy_pool()
    return jsonify({
        "total": len(merged_list),
        "results": merged_list,
        "cached_total": len(merged),
        "parser_stats": get_all_parser_stats(),
        "proxy": {
            "enabled": _pool.enabled,
            "count": len(_pool),
            "sites": _pool.usage(),
        },
    })


@app.route("/api/parser-status")
def api_parser_status():
    return jsonify({"parsers": get_all_parser_stats()})


@app.route("/api/rates")
def api_get_rates():
    """Return current exchange rates (KZT base) and update time."""
    r = rates_module.get_rates()
    return jsonify({
        "base": "KZT",
        "RUB": r.get("RUB"),
        "USD": r.get("USD"),
        "EUR": r.get("EUR"),
        "updated": r.get("updated", ""),
        "fetched_at": r.get("fetched_at", 0),
    })


@app.route("/api/rates/refresh", methods=["POST"])
def api_refresh_rates():
    """Force-refresh exchange rates from the online API."""
    r = rates_module.refresh_rates()
    return jsonify({
        "ok": True,
        "base": "KZT",
        "RUB": r.get("RUB"),
        "USD": r.get("USD"),
        "EUR": r.get("EUR"),
        "updated": r.get("updated", ""),
        "fetched_at": r.get("fetched_at", 0),
    })


@app.route("/api/results/clear", methods=["POST"])
def api_clear_results():
    """Clear the results cache so previously found apartments are removed."""
    if _RESULTS_CACHE.exists():
        _RESULTS_CACHE.unlink()
    return jsonify({"ok": True, "message": "Кэш результатов очищен"})


# ============================================================
# Background scheduler: refresh listings automatically every N hours.
# ============================================================
@app.route("/api/scheduler", methods=["GET"])
def api_get_scheduler():
    """Status snapshot: enabled, interval, last/next run timestamps."""
    try:
        import scheduler
        return jsonify(scheduler.get_state())
    except Exception as exc:
        log.warning("[scheduler] status failed: %s", exc)
        return jsonify({"error": str(exc)[:200]}), 500


@app.route("/api/scheduler", methods=["POST"])
def api_set_scheduler():
    """Update scheduler configuration: {enabled, interval_hours}.

    Persists to settings.json so the change survives restarts, then
    signals the running daemon thread immediately.
    """
    args = request.get_json(silent=True) or request.form
    data = load_settings()
    if "enabled" in args:
        data["scheduler_enabled"] = bool(args["enabled"])
    if "interval_hours" in args:
        try:
            pih = int(args["interval_hours"])
        except (TypeError, ValueError):
            pih = _DEFAULT_SETTINGS["parser_interval_hours"]
        data["parser_interval_hours"] = max(_SCHEDULER_INTERVAL_MIN,
                                            min(_SCHEDULER_INTERVAL_MAX, pih))
    save_settings(data)
    try:
        import scheduler
        state = scheduler.configure(
            enabled=data["scheduler_enabled"],
            interval_hours=data["parser_interval_hours"],
        )
        return jsonify(state)
    except Exception as exc:
        log.warning("[scheduler] configure failed: %s", exc)
        return jsonify({"error": str(exc)[:200]}), 500


@app.route("/api/scheduler/run", methods=["POST"])
def api_run_scheduler_now():
    """Trigger an immediate scheduler run (manual trigger).

    Returns the state snapshot immediately; the run happens asynchronously.
    The UI should poll ``GET /api/scheduler`` to see ``running=true`` →
    ``last_run_status`` afterwards.
    """
    try:
        import scheduler
        return jsonify(scheduler.run_now())
    except Exception as exc:
        log.warning("[scheduler] manual run failed: %s", exc)
        return jsonify({"error": str(exc)[:200]}), 500


@app.route("/api/results")
def api_get_results():
    """Return cached search results (without re-running parsers)."""
    prev = _load_prev_results()
    results = sorted(
        prev.values(),
        key=lambda r: (r.get("price") is None, r.get("price") or 0),
    )
    # Cached view: badges belong to the run that computed them, so a page
    # reload must not re-display stale "NEW"/"CHANGED" markers.
    _strip_volatile_flags(results)
    results = _filter_no_photo(results)
    return jsonify({
        "total": len(results),
        "results": results,
    })


@app.route("/api/districts")
def api_districts():
    return jsonify(get_districts_json())


@app.route("/api/2gis/agencies")
def api_2gis_agencies():
    """Discovered real-estate agencies (task_2gis_2do.md §12).

    Agencies are NOT listings — a separate endpoint mirrors how
    ``api_parser_status`` exposes parser telemetry. Supports free-text
    filtering by name/address/phone.
    """
    q = (request.args.get("q") or "").strip()
    agencies = db_list_organizations(q)
    return jsonify({"agencies": agencies, "total": len(agencies)})


@app.route("/api/2gis/buildings")
def api_2gis_buildings():
    """Cached building records (task_2gis_2do.md §1.2, §11)."""
    return jsonify({"buildings": db_list_buildings()})


@app.route("/api/heatmap", methods=["POST"])
def api_heatmap():
    """Возвращает данные для тепловой карты цен по районам."""
    args = request.get_json(silent=True) or request.form
    params = _parse_params(args)
    property_type = args.get("property_type", "all")

    filtered_params = SearchParams(**{**_dict(params), "limit": 0})
    if property_type in ("studio", "0"):
        filtered_params.rooms = [0]
    elif property_type in ("1", "1-room"):
        filtered_params.rooms = [1]
    elif property_type in ("2", "2-room"):
        filtered_params.rooms = [2]
    elif property_type == "apartment":
        filtered_params.query = "апартамент"

    parsers = get_all_parsers()
    _apply_parser_max_pages(parsers)
    # Heatmap only needs price + location, not photos. Skip the expensive
    # detail-page photo enrichment (1000 listings × HTTP fetch = multi-minute hang).
    for p in parsers:
        p.enrich_photo_count = 0
    log.info("[heatmap] dispatching %d parsers (property_type=%s, photos off)", len(parsers), property_type)
    results: list[Listing] = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(p.run, filtered_params): p.name for p in parsers}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                items = fut.result()
                results.extend(items)
                log.info("[heatmap] parser %s returned %d results", name, len(items))
            except Exception as exc:
                log.warning("[heatmap] parser %s failed: %s", name, exc, exc_info=True)
    log.info("[heatmap] total %d listings from %d parsers", len(results), len(parsers))

    districts_data = get_districts_json()
    # Pre-compute lowercased polygons for the point-in-polygon test.
    polygons = [(d["name"], d["polygon"]) for d in districts_data]
    by_district: dict[str, list[Listing]] = {d["name"]: [] for d in districts_data}
    unmatched: list[Listing] = []
    for item in results:
        # 1) Prefer exact coords: locate the district by point-in-polygon.
        #    This is reliable (krisha/olx/kn embed coords) and avoids the
        #    substring pitfalls of step 2.
        matched = None
        if item.lat is not None and item.lon is not None:
            for dname, poly in polygons:
                if _point_in_polygon(item.lat, item.lon, poly):
                    matched = dname
                    break
        # 2) Fall back to the address/title substring. Match the FULL
        #    district name with word boundaries so "ул. Ауэзова" does NOT
        #    match "Ауэзовский" (shared root, different word). Real addresses
        #    typically say "Алмалинский р-н" / "Бостандыкский район" which
        #    includes the full "-ский" form.
        if matched is None:
            hay = f"{item.address} {item.title}".lower()
            for dname in by_district:
                if re.search(r"\b" + re.escape(dname.lower()) + r"\b", hay):
                    matched = dname
                    break
        if matched:
            by_district[matched].append(item)
        else:
            unmatched.append(item)

    heat_points = []
    district_stats = []
    for d in districts_data:
        items = by_district[d["name"]]
        prices = [it.price for it in items if it.price]
        avg_price = int(sum(prices) / len(prices)) if prices else 0
        count = len(items)
        heat_points.append({
            "lat": d["lat"], "lon": d["lon"],
            "price": avg_price, "count": count,
            "district": d["name"],
        })
        district_stats.append({
            "name": d["name"],
            "avg_price": avg_price,
            "count": count,
            "lat": d["lat"], "lon": d["lon"],
            "polygon": d["polygon"],
            "description": d["description"],
        })

    max_price = max((p["price"] for p in heat_points if p["price"]), default=1) or 1
    for p in heat_points:
        p["intensity"] = round(p["price"] / max_price, 3) if p["price"] else 0
    for s in district_stats:
        s["intensity"] = round(s["avg_price"] / max_price, 3) if s["avg_price"] else 0

    return jsonify({
        "heat_points": heat_points,
        "district_stats": district_stats,
        "property_type": property_type,
        "total_listings": len(results),
        "unmatched_count": len(unmatched),
    })


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


@app.route("/api/export/txt", methods=["POST"])
def api_export_txt():
    items = _build_listings_from_request()
    data = export_txt(items)
    return Response(
        data, mimetype="text/plain",
        headers={"Content-Disposition": "attachment; filename=almaty_rent.txt"}
    )


@app.route("/api/export/pdf", methods=["POST"])
def api_export_pdf():
    args = request.get_json(silent=True) or request.form
    items = _build_listings_from_request()
    orientation = args.get("orientation", "portrait")
    data = export_pdf(items, orientation=orientation)
    fname = f"almaty_rent_{'portrait' if orientation == 'portrait' else 'landscape'}.pdf"
    return Response(
        data, mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'}
    )


@app.route("/api/photo-cache", methods=["GET"])
def api_photo_cache_status():
    """Return the current on-disk photo cache size (bytes) and file count."""
    from export_utils import _cache_dir_stats
    stats = _cache_dir_stats()
    return jsonify({
        "files": stats["files"],
        "size_bytes": stats["size_bytes"],
        "size_mb": round(stats["size_bytes"] / 1024 / 1024, 1),
        "max_mb": load_settings().get("photo_cache_mb", 500),
    })


@app.route("/api/photo-cache/clear", methods=["POST"])
def api_photo_cache_clear():
    """Delete all cached photos from disk."""
    from export_utils import _cache_clear
    deleted = _cache_clear()
    log.info("photo cache cleared: %d files deleted", deleted)
    return jsonify({"ok": True, "deleted": deleted})


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


# ============================================================
# Favorites API
# ============================================================

@app.route("/api/favorites", methods=["GET"])
def api_list_favorites():
    favs = db_list_favorites()
    rub_rate = rates_module.rub_per_kzt()
    usd_rate = rates_module.usd_per_kzt()
    for f in favs:
        price = f.get("price")
        if price is not None:
            f["price_str"] = f"{price:,} {f.get('currency', 'тг')}".replace(",", " ")
            f["price_rub"] = float(round(price * rub_rate))
            f["price_usd"] = round(price * usd_rate, 2)
        else:
            f["price_str"] = "—"
            f["price_rub"] = None
            f["price_usd"] = None
        hist = db_get_price_history(f["listing_key"])
        f["price_history"] = hist
        f["price_change"] = _compute_price_change(hist)
    return jsonify({"total": len(favs), "favorites": favs})


@app.route("/api/favorites", methods=["POST"])
def api_add_favorite():
    args = request.get_json(silent=True) or request.form
    required = ["url", "source", "title"]
    for field in required:
        if not args.get(field):
            return jsonify({"error": f"Missing field: {field}"}), 400
    if not args.get("listing_key"):
        args["listing_key"] = listing_key(args["url"], args["source"], args["title"])
    data = args.to_dict() if hasattr(args, "to_dict") else dict(args)
    existing = db_get_favorite(data["listing_key"])
    if existing:
        return jsonify({"ok": True, "favorite": existing, "already_exists": True})
    fav = db_add_favorite(data)
    return jsonify({"ok": True, "favorite": fav, "already_exists": False})


@app.route("/api/favorites/<key>", methods=["DELETE"])
def api_delete_favorite(key: str):
    deleted = db_delete_favorite(key)
    return jsonify({"ok": deleted, "listing_key": key})


@app.route("/api/favorites/<key>/rate", methods=["PUT"])
def api_rate_favorite(key: str):
    args = request.get_json(silent=True) or request.form
    rating = args.get("rating")
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        return jsonify({"error": "Rating must be an integer 0-5"}), 400
    if not (0 <= rating <= 5):
        return jsonify({"error": "Rating must be 0-5"}), 400
    fav = db_update_rating(key, rating)
    if fav is None:
        return jsonify({"error": "Favorite not found"}), 404
    return jsonify({"ok": True, "favorite": fav})


@app.route("/api/favorites/<key>/comment", methods=["PUT"])
def api_comment_favorite(key: str):
    args = request.get_json(silent=True) or request.form
    comment = args.get("comment", "")
    if comment is None:
        comment = ""
    fav = db_update_comment(key, str(comment))
    if fav is None:
        return jsonify({"error": "Favorite not found"}), 404
    return jsonify({"ok": True, "favorite": fav})


@app.route("/api/favorites/<key>/history", methods=["GET"])
def api_price_history(key: str):
    history = db_get_price_history(key)
    fav = db_get_favorite(key)
    if fav is None:
        return jsonify({"error": "Favorite not found"}), 404
    change = _compute_price_change(history)
    return jsonify({
        "listing_key": key,
        "title": fav.get("title", ""),
        "current_price": fav.get("price"),
        "history": history,
        "price_change": change,
    })


@app.route("/api/favorites/check-prices", methods=["POST"])
def api_check_prices():
    results = db_check_prices()
    changed = [r for r in results if r.get("changed")]
    return jsonify({
        "checked": len(results),
        "changed_count": len(changed),
        "results": results,
    })


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


# Start the background scheduler if it was enabled in settings.
# Skip the autostart when running under pytest — tests that need the
# scheduler configure it explicitly (and would falsely trigger real parser
# network runs at import time otherwise).
if "pytest" not in sys.modules:
    try:
        import scheduler
        scheduler.start_from_settings(load_settings())
    except Exception as exc:
        log.warning("[scheduler] failed to start: %s", exc)


# Sync the shared free-proxy pool with persisted settings at startup so a
# restart picks up the enabled flag + sources without a manual settings call.
try:
    get_proxy_pool().configure(load_settings().get("proxy"))
except Exception as _exc:  # pragma: no cover - startup best-effort
    log.warning("[proxy] startup configure failed: %s", _exc)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Guard against a previously started instance that is still alive on the
    # port. Werkzeug binds with SO_REUSEADDR, so a second `python app.py`
    # would "start" fine while the old process keeps answering the requests —
    # its logs then go to the old (often closed) terminal and the new one
    # shows nothing. Fail fast with a hint instead.
    _PORT = 5000
    if not _port_is_free(_PORT):
        owner = _port_owner_pids(_PORT)
        print(
            f"\nПорт {_PORT} уже занят (PID {owner or 'неизвестен'}): "
            f"предыдущий экземпляр этого приложения всё ещё работает.\n"
            f"Запросы обрабатывает он, поэтому его логи выводятся в тот "
            f"терминал, где он запущен, а в этом терминале логов не видно.\n"
            f"Закройте старый процесс и запустите заново:\n"
            f"    taskkill /F /PID {owner or '<PID>'}\n",
            file=sys.stderr,
        )
        sys.exit(1)
    # debug=False: the Werkzeug debugger allows arbitrary code execution and
    # the app binds to 0.0.0.0, so a debug instance must never be exposed.
    # threaded=True so a long PDF export (which downloads many photos) does
    # not block the rest of the app for other requests.
    app.run(debug=False, host="0.0.0.0", port=_PORT, threaded=True)
