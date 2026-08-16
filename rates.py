"""Online currency exchange rates for KZT → RUB / USD.

Rates are fetched from open.er-api.com (free, no API key) and cached in
``data/rates.json`` for one hour. If the API is unreachable the last cached
values are used; if no cache exists, hardcoded fallback rates are returned.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("rates")

_RATES_PATH = Path(__file__).parent / "data" / "rates.json"
_CACHE_TTL = 3600  # 1 hour

# Fallback rates (KZT → target): 1 KZT = rate * target
# Updated Aug 2026; used only when both API and cache are unavailable.
_FALLBACK = {
    "KZT": 1.0,
    "RUB": 0.1791,
    "USD": 0.002148,
    "EUR": 0.001864,
    "updated": "fallback",
}

_lock = threading.RLock()  # reentrant: refresh_rates() calls get_rates()
_cached: Optional[dict] = None  # {rates: {...}, fetched_at: ts}


def _load_cache() -> Optional[dict]:
    if not _RATES_PATH.exists():
        return None
    try:
        return json.loads(_RATES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_cache(data: dict) -> None:
    try:
        _RATES_PATH.parent.mkdir(parents=True, exist_ok=True)
        _RATES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Cannot save rates cache: %s", exc)


def _fetch_online() -> Optional[dict]:
    """Fetch fresh rates from open.er-api.com. Returns None on failure."""
    try:
        resp = requests.get("https://open.er-api.com/v6/latest/KZT", timeout=10)
        resp.raise_for_status()
        body = resp.json()
        if body.get("result") != "success":
            return None
        rates_raw = body.get("rates", {})
        return {
            "KZT": 1.0,
            "RUB": float(rates_raw.get("RUB", _FALLBACK["RUB"])),
            "USD": float(rates_raw.get("USD", _FALLBACK["USD"])),
            "EUR": float(rates_raw.get("EUR", _FALLBACK["EUR"])),
            "updated": body.get("time_last_update_utc", ""),
            "next_update": body.get("time_next_update_utc", ""),
            "fetched_at": int(time.time()),
        }
    except Exception as exc:
        log.warning("Failed to fetch rates: %s", exc)
        return None


def get_rates() -> dict:
    """Return current rates dict (KZT base).

    Structure: ``{KZT, RUB, USD, EUR, updated, fetched_at}``.
    Refreshes from the API at most once per hour; otherwise uses cache.
    """
    global _cached
    with _lock:
        now = int(time.time())
        # In-memory cache fresh?
        if _cached and (now - _cached.get("fetched_at", 0)) < _CACHE_TTL:
            return _cached
        # File cache fresh?
        file_cache = _load_cache()
        if file_cache and (now - file_cache.get("fetched_at", 0)) < _CACHE_TTL:
            _cached = file_cache
            return _cached
        # Fetch online
        fresh = _fetch_online()
        if fresh:
            _save_cache(fresh)
            _cached = fresh
            return fresh
        # Stale file cache?
        if file_cache:
            _cached = file_cache
            return file_cache
        # Fallback
        _cached = {**_FALLBACK, "fetched_at": now}
        return _cached


def refresh_rates() -> dict:
    """Force a fresh fetch (ignoring TTL). Returns new rates."""
    global _cached
    with _lock:
        fresh = _fetch_online()
        if fresh:
            _save_cache(fresh)
            _cached = fresh
            return fresh
        # Keep existing cache / fallback
        return get_rates()


def rub_per_kzt() -> float:
    return get_rates().get("RUB", _FALLBACK["RUB"])


def usd_per_kzt() -> float:
    return get_rates().get("USD", _FALLBACK["USD"])
