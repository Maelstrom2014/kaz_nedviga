"""Search, cached results, parser status and cache-reset routes."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from parsers.models import Listing
from .. import core

bp = Blueprint("search", __name__)


def _annotate_district(rows: list[dict]) -> None:
    """Определить район по координатам (point-in-polygon) → ``district_name``
    и расстояние до ближайшего метро → ``metro``.

    Текстовый поиск имени района в адресе ненадёжен: реальные адреса
    («ул. Кунаева, 25») почти никогда не содержат название района.
    Аннотация проставляется только на ответе — кэш не изменяется.
    """
    try:
        from geo.districts import DISTRICTS

        for r in rows:
            if not isinstance(r, dict) or r.get("district_name"):
                continue
            lat, lon = r.get("lat"), r.get("lon")
            if lat is None or lon is None:
                continue
            for d in DISTRICTS:
                if core._point_in_polygon(lat, lon, d["polygon"]):
                    r["district_name"] = d["name"]
                    break
            try:
                from geo.metro import nearest_metro

                station, dist_m = nearest_metro(lat, lon)
                if station:
                    r["metro"] = {"name": station["name"],
                                  "distance_m": int(round(dist_m))}
            except Exception:
                pass
    except Exception as exc:
        core.log.debug("[search] district annotation skipped: %s", exc)


def _annotate_price_eval(rows: list[dict]) -> None:
    """Добавить оценку «хорошая цена» от ML-модели (если обучена)."""
    try:
        from ml.scorer import annotate_rows
        annotate_rows(rows)
    except Exception as exc:
        core.log.debug("[search] price scoring skipped: %s", exc)


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
    _annotate_district(merged_list)
    _annotate_price_eval(merged_list)

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
    _annotate_district(results)
    _annotate_price_eval(results)
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
