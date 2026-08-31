"""Search, cached results, heatmap, parser status and cache-reset routes."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Blueprint, jsonify, request

from parsers.models import Listing, SearchParams
from .. import core

bp = Blueprint("search", __name__)


@bp.route("/api/search", methods=["POST"])
def api_search():
    args = request.get_json(silent=True) or request.form
    params = core._parse_params(args)
    selected_sources = args.get("sources", [])
    parsers = core.get_all_parsers()
    core._apply_parser_max_pages(parsers)
    if selected_sources:
        parsers = [p for p in parsers if p.name in selected_sources]

    core.get_proxy_pool().clear_usage()
    results: list[Listing] = core._run_all_parsers(parsers, params)
    results.sort(key=lambda x: (x.price is None, x.price or 0))

    # Building/geo enrichment (task_2gis_2do.md §1.2, §11): fill missing
    # residential_complex / microdistrict / building coords from cached
    # building records + district polygons. Best-effort, never blocks search.
    try:
        from geo.buildings import enrich_listings
        enrich_listings(results)
    except Exception as exc:
        core.log.debug("[search] building enrichment skipped: %s", exc)

    # Merge with previous results and save
    result_dicts = [core._to_dict(r) for r in results]
    prev = core._load_prev_results()
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
    merged = core._save_results(result_dicts)

    # Return the MERGED set (previous + current) so previously found
    # apartments don't disappear from the UI on the next search.
    current_keys = {f"{r.get('source','')}|{r.get('url','')}" for r in result_dicts}
    merged_list = []
    cached_cards = []
    for key, r in merged.items():
        if key not in current_keys:
            # Not re-fetched in this run: drop one-shot badges from the
            # previous run, otherwise "NEW"/"CHANGED" would stick forever.
            core._strip_volatile_flags([r])
            cached_cards.append(r)
        merged_list.append(r)
    # Re-check a bounded subset of cached cards for validity (removed/expired
    # ads) and mark them on the card — the same info the favorites show.
    # Persist the results so the badges survive a reload.
    if cached_cards:
        core._check_cached_cards(cached_cards,
                                 {p.name: p for p in core.get_all_parsers()})
        core._write_results_cache(merged)
    merged_list.sort(key=lambda r: (r.get("price") is None, r.get("price") or 0))
    merged_list = core._filter_no_photo(merged_list)

    _pool = core.get_proxy_pool()
    return jsonify({
        "total": len(merged_list),
        "results": merged_list,
        "cached_total": len(merged),
        "parser_stats": core.get_all_parser_stats(),
        "proxy": {
            "enabled": _pool.enabled,
            "count": len(_pool),
            "sites": _pool.usage(),
        },
    })


@bp.route("/api/results")
def api_get_results():
    """Return cached search results (without re-running parsers)."""
    prev = core._load_prev_results()
    results = sorted(
        prev.values(),
        key=lambda r: (r.get("price") is None, r.get("price") or 0),
    )
    # Cached view: badges belong to the run that computed them, so a page
    # reload must not re-display stale "NEW"/"CHANGED" markers.
    core._strip_volatile_flags(results)
    results = core._filter_no_photo(results)
    return jsonify({
        "total": len(results),
        "results": results,
    })


@bp.route("/api/results/clear", methods=["POST"])
def api_clear_results():
    """Clear the results cache so previously found apartments are removed."""
    if core._RESULTS_CACHE.exists():
        core._RESULTS_CACHE.unlink()
    return jsonify({"ok": True, "message": "Кэш результатов очищен"})


@bp.route("/api/parser-status")
def api_parser_status():
    return jsonify({"parsers": core.get_all_parser_stats()})


@bp.route("/api/database/reset", methods=["POST"])
def api_reset_database():
    """Delete the entire database file (favorites + price history) and clear cached results."""
    core.reset_db()
    if core._RESULTS_CACHE.exists():
        core._RESULTS_CACHE.unlink()
    return jsonify({"ok": True, "message": "База данных и кэш результатов удалены"})


@bp.route("/api/favorites/clear", methods=["POST"])
def api_clear_favorites():
    """Delete all favorites but keep the database file and schema."""
    removed = core.db_clear_favorites()
    if core._RESULTS_CACHE.exists():
        core._RESULTS_CACHE.unlink()
    data = core.load_settings()
    return jsonify({"ok": True, "removed": removed,
                    "theme": data.get("theme", core._DEFAULT_THEME)})


@bp.route("/api/heatmap", methods=["POST"])
def api_heatmap():
    """Возвращает данные для тепловой карты цен по районам."""
    args = request.get_json(silent=True) or request.form
    params = core._parse_params(args)
    property_type = args.get("property_type", "all")

    filtered_params = SearchParams(**{**core._dict(params), "limit": 0})
    if property_type in ("studio", "0"):
        filtered_params.rooms = [0]
    elif property_type in ("1", "1-room"):
        filtered_params.rooms = [1]
    elif property_type in ("2", "2-room"):
        filtered_params.rooms = [2]
    elif property_type == "apartment":
        filtered_params.query = "апартамент"

    parsers = core.get_all_parsers()
    core._apply_parser_max_pages(parsers)
    # Heatmap only needs price + location, not photos. Skip the expensive
    # detail-page photo enrichment (1000 listings × HTTP fetch = multi-minute hang).
    for p in parsers:
        p.enrich_photo_count = 0
    core.log.info("[heatmap] dispatching %d parsers (property_type=%s, photos off)",
                  len(parsers), property_type)
    results: list[Listing] = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(p.run, filtered_params): p.name for p in parsers}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                items = fut.result()
                results.extend(items)
                core.log.info("[heatmap] parser %s returned %d results", name, len(items))
            except Exception as exc:
                core.log.warning("[heatmap] parser %s failed: %s", name, exc, exc_info=True)
    core.log.info("[heatmap] total %d listings from %d parsers", len(results), len(parsers))

    districts_data = core.get_districts_json()
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
                if core._point_in_polygon(item.lat, item.lon, poly):
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
