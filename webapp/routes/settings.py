"""Index page + settings + search defaults + districts routes."""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from parsers.proxy import DEFAULT_PROXY_SETTINGS, sanitize_proxy_settings
from .. import core

bp = Blueprint("settings", __name__)


@bp.route("/")
def index():
    return render_template(
        "index.html",
        sites=core.list_sites(),
        districts=core.get_district_list(),
        themes=core.THEMES,
        theme=core.load_settings().get("theme", core._DEFAULT_THEME),
    )


@bp.route("/api/settings", methods=["GET"])
def api_get_settings():
    data = core.load_settings()
    # Normalize so clients always see every known key
    data.setdefault("hide_no_photo", False)
    data.setdefault("crawl_sources", core._DEFAULT_SETTINGS["crawl_sources"])
    data.setdefault("results_columns", core._DEFAULT_SETTINGS["results_columns"])
    data.setdefault("card_width_scale", core._DEFAULT_SETTINGS["card_width_scale"])
    data.setdefault("photo_cache_mb", core._DEFAULT_SETTINGS["photo_cache_mb"])
    data.setdefault("scheduler_enabled", False)
    data.setdefault("parser_interval_hours",
                    core._DEFAULT_SETTINGS["parser_interval_hours"])
    data.setdefault("bot_enabled", False)
    data.setdefault("proxy", DEFAULT_PROXY_SETTINGS)
    core.get_proxy_pool().configure(data.get("proxy"))
    return jsonify(data)


@bp.route("/api/settings", methods=["POST"])
def api_set_settings():
    args = request.get_json(silent=True) or request.form
    data = core.load_settings()
    if "theme" in args:
        theme = args.get("theme")
        if theme not in core.THEMES:
            return jsonify({"error": f"Unknown theme. Available: {core.THEMES}"}), 400
        data["theme"] = theme
    if "hide_no_photo" in args:
        data["hide_no_photo"] = bool(args["hide_no_photo"])
    if "crawl_sources" in args:
        cs = args.get("crawl_sources")
        if not isinstance(cs, list):
            return jsonify({"error": "crawl_sources must be a list"}), 400
        known = {s["name"] for s in core.list_sites()}
        # Unknown/renamed sites are dropped; empty list = all sites.
        data["crawl_sources"] = [str(x) for x in cs if str(x) in known]
    if "results_columns" in args:
        try:
            rc = int(args["results_columns"])
        except (TypeError, ValueError):
            rc = core._DEFAULT_SETTINGS["results_columns"]
        data["results_columns"] = max(core._RESULTS_COLUMNS_MIN,
                                      min(core._RESULTS_COLUMNS_MAX, rc))
    if "card_width_scale" in args:
        try:
            cws = int(args["card_width_scale"])
        except (TypeError, ValueError):
            cws = core._DEFAULT_SETTINGS["card_width_scale"]
        data["card_width_scale"] = max(core._CARD_WIDTH_SCALE_MIN,
                                       min(core._CARD_WIDTH_SCALE_MAX, cws))
    if "olx_phone_page_only" in args:
        data["olx_phone_page_only"] = bool(args["olx_phone_page_only"])
    if "olx_phone_playwright" in args:
        data["olx_phone_playwright"] = bool(args["olx_phone_playwright"])
    if "photo_cache_mb" in args:
        try:
            pcm = int(args["photo_cache_mb"])
        except (TypeError, ValueError):
            pcm = core._DEFAULT_SETTINGS["photo_cache_mb"]
        data["photo_cache_mb"] = max(core._PHOTO_CACHE_MB_MIN,
                                     min(core._PHOTO_CACHE_MB_MAX, pcm))
    if "scheduler_enabled" in args:
        data["scheduler_enabled"] = bool(args["scheduler_enabled"])
    if "bot_enabled" in args:
        data["bot_enabled"] = bool(args["bot_enabled"])
    if "parser_interval_hours" in args:
        try:
            pih = int(args["parser_interval_hours"])
        except (TypeError, ValueError):
            pih = core._DEFAULT_SETTINGS["parser_interval_hours"]
        data["parser_interval_hours"] = max(core._SCHEDULER_INTERVAL_MIN,
                                            min(core._SCHEDULER_INTERVAL_MAX, pih))
    if "parser_max_pages" in args:
        pmp = args.get("parser_max_pages")
        if not isinstance(pmp, dict):
            return jsonify({"error": "parser_max_pages must be an object"}), 400
        base = dict(core._DEFAULT_SETTINGS["parser_max_pages"])
        for name, val in pmp.items():
            try:
                n = int(val)
            except (TypeError, ValueError):
                continue
            if core._MAX_PAGES_MIN <= n <= core._MAX_PAGES_MAX:
                base[name] = n
        data["parser_max_pages"] = base
    if "proxy" in args:
        data["proxy"] = sanitize_proxy_settings(args.get("proxy"))
    core.save_settings(data)
    # Apply proxy config to the shared pool immediately.
    core.get_proxy_pool().configure(data.get("proxy"))
    # Apply scheduler config changes to the running daemon immediately.
    try:
        import scheduler
        scheduler.configure(
            enabled=data.get("scheduler_enabled", False),
            interval_hours=data.get("parser_interval_hours",
                                    core._DEFAULT_SETTINGS["parser_interval_hours"]),
        )
    except Exception as exc:
        core.log.warning("[settings] scheduler reconfigure failed: %s", exc)
    # Apply the telegram-bot toggle to the running bot thread immediately.
    try:
        import telegram_bot
        telegram_bot.configure(data.get("bot_enabled", False))
    except Exception as exc:
        core.log.warning("[settings] bot reconfigure failed: %s", exc)
    return jsonify({"ok": True,
                    "theme": data.get("theme", core._DEFAULT_THEME),
                    "hide_no_photo": bool(data.get("hide_no_photo", False)),
                    "crawl_sources": data.get("crawl_sources", []),
                    "results_columns": data.get(
                        "results_columns",
                        core._DEFAULT_SETTINGS["results_columns"]),
                    "card_width_scale": data.get(
                        "card_width_scale",
                        core._DEFAULT_SETTINGS["card_width_scale"]),
                    "photo_cache_mb": data.get("photo_cache_mb",
                                               core._DEFAULT_SETTINGS["photo_cache_mb"]),
                    "scheduler_enabled": bool(data.get("scheduler_enabled", False)),
                    "bot_enabled": bool(data.get("bot_enabled", False)),
                    "parser_interval_hours": data.get(
                        "parser_interval_hours",
                        core._DEFAULT_SETTINGS["parser_interval_hours"]),
                    "parser_max_pages": data.get("parser_max_pages", {})})


@bp.route("/api/search-defaults", methods=["GET"])
def api_get_search_defaults():
    return jsonify(core.load_settings().get(
        "search_defaults", core._DEFAULT_SEARCH_DEFAULTS))


@bp.route("/api/search-defaults", methods=["POST"])
def api_set_search_defaults():
    """Persist default search parameters (price_min/max, rooms, district, etc.).

    All fields are replaced atomically: if a field is missing or null it is
    cleared. This keeps the semantics simple and predictable — the front-end
    always submits the full form.
    """
    args = request.get_json(silent=True) or request.form
    data = core.load_settings()
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
    core.save_settings(data)
    return jsonify({"ok": True, "search_defaults": sd})


@bp.route("/api/districts")
def api_districts():
    return jsonify(core.get_districts_json())
