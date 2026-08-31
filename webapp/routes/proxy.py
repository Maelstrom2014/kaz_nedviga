"""Proxy pool status/config routes."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from parsers.proxy import PROXY_SOURCES, KNOWN_SITES, sanitize_proxy_settings
from .. import core

bp = Blueprint("proxy", __name__)


@bp.route("/api/proxy", methods=["GET"])
def api_proxy_status():
    """Proxy pool config + status (count, last refresh, per-source stats)."""
    data = core.load_settings()
    pool = core.get_proxy_pool()
    pool.configure(data.get("proxy"))
    st = pool.status()
    st["config"] = sanitize_proxy_settings(data.get("proxy"))
    st["sources"] = PROXY_SOURCES
    st["known_sites"] = list(KNOWN_SITES)
    st["proxies"] = [e.to_dict() for e in pool.snapshot()]
    st["errors"] = pool.errors(100)
    st["usage"] = pool.usage()
    return jsonify(st)


@bp.route("/api/proxy", methods=["POST"])
def api_proxy_update():
    """Update proxy settings (full or partial) and sync the shared pool."""
    args = request.get_json(silent=True) or request.form
    data = core.load_settings()
    if "proxy" in args:
        data["proxy"] = sanitize_proxy_settings(args.get("proxy"))
    elif args:
        merged = dict(data.get("proxy") or {})
        merged.update(args)
        data["proxy"] = sanitize_proxy_settings(merged)
    core.save_settings(data)
    core.get_proxy_pool().configure(data.get("proxy"))
    return jsonify({"ok": True, "config": data["proxy"],
                    "status": core.get_proxy_pool().status()})


@bp.route("/api/proxy/refresh", methods=["POST"])
def api_proxy_refresh():
    """Fetch proxy lists from the enabled sources and test which ones work."""
    args = request.get_json(silent=True) or request.form
    data = core.load_settings()
    if args and "proxy" in args:
        data["proxy"] = sanitize_proxy_settings(args.get("proxy"))
    cfg = sanitize_proxy_settings(data.get("proxy"))
    pool = core.get_proxy_pool()
    try:
        stats = pool.refresh(cfg)
    except Exception as exc:
        core.log.warning("[proxy] refresh failed: %s", exc, exc_info=True)
        return jsonify({"ok": False, "error": str(exc)}), 500
    core.save_settings(data)
    return jsonify({"ok": True, "stats": stats, "status": pool.status()})


@bp.route("/api/proxy/clear", methods=["POST"])
def api_proxy_clear():
    core.get_proxy_pool().clear()
    return jsonify({"ok": True, "status": core.get_proxy_pool().status()})


@bp.route("/api/proxy/errors/clear", methods=["POST"])
def api_proxy_clear_errors():
    core.get_proxy_pool().clear_errors()
    return jsonify({"ok": True,
                    "error_count": core.get_proxy_pool().status()["error_count"]})
