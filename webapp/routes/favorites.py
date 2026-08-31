"""Favorites CRUD, price history/checks, export and photo-cache routes."""
from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from .. import core

bp = Blueprint("favorites", __name__)


# ---------------- Export ----------------
@bp.route("/api/export/txt", methods=["POST"])
def api_export_txt():
    items = core._build_listings_from_request()
    data = core.export_txt(items)
    return Response(
        data, mimetype="text/plain",
        headers={"Content-Disposition": "attachment; filename=almaty_rent.txt"}
    )


@bp.route("/api/export/pdf", methods=["POST"])
def api_export_pdf():
    args = request.get_json(silent=True) or request.form
    items = core._build_listings_from_request()
    orientation = args.get("orientation", "portrait")
    data = core.export_pdf(items, orientation=orientation)
    fname = f"almaty_rent_{'portrait' if orientation == 'portrait' else 'landscape'}.pdf"
    return Response(
        data, mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'}
    )


# ---------------- Photo cache ----------------
@bp.route("/api/photo-cache", methods=["GET"])
def api_photo_cache_status():
    """Return the current on-disk photo cache size (bytes) and file count."""
    from export_utils import _cache_dir_stats
    stats = _cache_dir_stats()
    return jsonify({
        "files": stats["files"],
        "size_bytes": stats["size_bytes"],
        "size_mb": round(stats["size_bytes"] / 1024 / 1024, 1),
        "max_mb": core.load_settings().get("photo_cache_mb", 500),
    })


@bp.route("/api/photo-cache/clear", methods=["POST"])
def api_photo_cache_clear():
    """Delete all cached photos from disk."""
    from export_utils import _cache_clear
    deleted = _cache_clear()
    core.log.info("photo cache cleared: %d files deleted", deleted)
    return jsonify({"ok": True, "deleted": deleted})


# ---------------- Favorites ----------------
@bp.route("/api/favorites", methods=["GET"])
def api_list_favorites():
    favs = core.db_list_favorites()
    rub_rate = core.rates_module.rub_per_kzt()
    usd_rate = core.rates_module.usd_per_kzt()
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
        hist = core.db_get_price_history(f["listing_key"])
        f["price_history"] = hist
        f["price_change"] = core._compute_price_change(hist)
    return jsonify({"total": len(favs), "favorites": favs})


@bp.route("/api/favorites", methods=["POST"])
def api_add_favorite():
    args = request.get_json(silent=True) or request.form
    required = ["url", "source", "title"]
    for field in required:
        if not args.get(field):
            return jsonify({"error": f"Missing field: {field}"}), 400
    if not args.get("listing_key"):
        args["listing_key"] = core.listing_key(
            args["url"], args["source"], args["title"])
    data = args.to_dict() if hasattr(args, "to_dict") else dict(args)
    existing = core.db_get_favorite(data["listing_key"])
    if existing:
        return jsonify({"ok": True, "favorite": existing, "already_exists": True})
    fav = core.db_add_favorite(data)
    return jsonify({"ok": True, "favorite": fav, "already_exists": False})


@bp.route("/api/favorites/<key>", methods=["DELETE"])
def api_delete_favorite(key: str):
    deleted = core.db_delete_favorite(key)
    return jsonify({"ok": deleted, "listing_key": key})


@bp.route("/api/favorites/<key>/rate", methods=["PUT"])
def api_rate_favorite(key: str):
    args = request.get_json(silent=True) or request.form
    rating = args.get("rating")
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        return jsonify({"error": "Rating must be an integer 0-5"}), 400
    if not (0 <= rating <= 5):
        return jsonify({"error": "Rating must be an integer 0-5"}), 400
    fav = core.db_update_rating(key, rating)
    if fav is None:
        return jsonify({"error": "Favorite not found"}), 404
    return jsonify({"ok": True, "favorite": fav})


@bp.route("/api/favorites/<key>/comment", methods=["PUT"])
def api_comment_favorite(key: str):
    args = request.get_json(silent=True) or request.form
    comment = args.get("comment", "")
    if comment is None:
        comment = ""
    fav = core.db_update_comment(key, str(comment))
    if fav is None:
        return jsonify({"error": "Favorite not found"}), 404
    return jsonify({"ok": True, "favorite": fav})


@bp.route("/api/favorites/<key>/history", methods=["GET"])
def api_price_history(key: str):
    history = core.db_get_price_history(key)
    fav = core.db_get_favorite(key)
    if fav is None:
        return jsonify({"error": "Favorite not found"}), 404
    change = core._compute_price_change(history)
    return jsonify({
        "listing_key": key,
        "title": fav.get("title", ""),
        "current_price": fav.get("price"),
        "history": history,
        "price_change": change,
    })


@bp.route("/api/favorites/check-prices", methods=["POST"])
def api_check_prices():
    results = core.db_check_prices()
    changed = [r for r in results if r.get("changed")]
    return jsonify({
        "checked": len(results),
        "changed_count": len(changed),
        "results": results,
    })
