"""Exchange-rate routes."""
from __future__ import annotations

from flask import Blueprint, jsonify

from .. import core

bp = Blueprint("rates", __name__)


@bp.route("/api/rates")
def api_get_rates():
    """Return current exchange rates (KZT base) and update time."""
    r = core.rates_module.get_rates()
    return jsonify({
        "base": "KZT",
        "RUB": r.get("RUB"),
        "USD": r.get("USD"),
        "EUR": r.get("EUR"),
        "updated": r.get("updated", ""),
        "fetched_at": r.get("fetched_at", 0),
    })


@bp.route("/api/rates/refresh", methods=["POST"])
def api_refresh_rates():
    """Force-refresh exchange rates from the online API."""
    r = core.rates_module.refresh_rates()
    return jsonify({
        "ok": True,
        "base": "KZT",
        "RUB": r.get("RUB"),
        "USD": r.get("USD"),
        "EUR": r.get("EUR"),
        "updated": r.get("updated", ""),
        "fetched_at": r.get("fetched_at", 0),
    })
