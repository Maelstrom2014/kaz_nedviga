"""Building + geo enrichment for listings (task_2gis_2do.md §1.2, §11).

A stateless post-run pass run from ``app.py:api_search`` after all parsers
return: for each ``Listing`` without ``residential_complex``/``microdistrict``
or with raw coordinates, resolve a known building record (via
``building_id`` or nearest district polygon) and fill the missing geo fields.

This is the 2GIS layer (§10, §11): the most undervalued part of the source —
tying a listing to a building gives median-rent-by-ЖК analytics downstream.

No OCR / no LLM: pure SQLite lookups + existing ``data/districts.py``
polygons via ``app``'s ``_point_in_polygon``. Imported lazily so the module
stays importable from contexts without the Flask app.
"""
from __future__ import annotations

import logging
from typing import Optional

from parsers.models import Listing

log = logging.getLogger("buildings")

# Representative Almaty residential complexes keyed by lowercase lookup
# fragments found in addresses/descriptions. Extend freely; matches are
# case-insensitive substring.
_ZHK_INDEX: dict[str, str] = {
    "алма сити": "Алма Сити",
    "almacity": "Алма Сити",
    "мега тауэр": "Mega Tower",
    "mega tower": "Mega Tower",
    "айнабулак": "Айнабулак",
    "нан": "НАН",
    "атакент": "Атакент",
    "багратион": "Багратион",
    "возрождение": "Возрождение",
    "казахстан-1": "Казахстан-1",
    "тайтұрлыс": "Тайтyрлыс",
}

# Microdistrict fragments → canonical names (Алматы).
_MICRO_INDEX: dict[str, str] = {
    "аксай": "Аксай",
    "тайгуль": "Таугуль",
    "таугуль": "Таугуль",
    "мамыр": "Мамыр",
    "аэропорт": "Аэропорт",
    "бутлер": "Бутлер",
    "каменка": "Каменка",
    "кольсай": "Кольсай",
    "或少": "Shanyrak",
    "шанырак": "Шанырак",
    "1-й алматы": "1-й Алматы",
    "2-й алматы": "2-й Алматы",
    "3-й алматы": "3-й Алматы",
}


def enrich_listings(listings: list[Listing]) -> None:
    """Fill ``residential_complex``/``microdistrict``/``district`` in place.

    Order of precedence per field:
      residential_complex: explicit Listing field > building record > ЖК index
      microdistrict:      building record > microdistrict index
      district:           building record > point-in-polygon on lat/lon
    """
    from db import get_building
    for l in listings:
        try:
            _enrich_one(l, get_building)
        except Exception as exc:
            log.debug("[buildings] enrich failed for %s: %s",
                      (l.url or l.title)[:60], exc)


def _enrich_one(l: Listing, get_building_fn) -> None:
    bld = None
    if l.building_id:
        bld = get_building_fn(l.building_id)
    if bld:
        if not l.residential_complex:
            l.residential_complex = bld.get("residential_complex") or None
        if not l.microdistrict:
            l.microdistrict = bld.get("microdistrict") or None
        if not l.address:
            l.address = bld.get("address") or ""
        if l.lat is None and bld.get("lat") is not None:
            l.lat = bld.get("lat")
        if l.lon is None and bld.get("lon") is not None:
            l.lon = bld.get("lon")
        if l.total_floors is None and bld.get("floors_total"):
            l.total_floors = bld.get("floors_total")

    # Residential complex: regex already filled it in twogis; if still empty,
    # try the address/description substring index.
    if not l.residential_complex:
        rc = _index_lookup(l.address + " " + l.description, _ZHK_INDEX)
        if rc:
            l.residential_complex = rc

    # Microdistrict via index if still empty.
    if not l.microdistrict:
        md = _index_lookup(l.address + " " + l.description, _MICRO_INDEX)
        if md:
            l.microdistrict = md

    # District via point-in-polygon on coordinates (defers import so this
    # module is safe to import from tests without the Flask app).
    if not getattr(l, "district", None) and l.lat is not None and l.lon is not None:
        d = _district_for_point(l.lat, l.lon)
        if d:
            # ``district`` is a SearchParams/dynamic concept; store on a
            # private attribute the UI/API can surface via to_dict if added.
            setattr(l, "_district", d)


def _index_lookup(text: str, index: dict[str, str]) -> Optional[str]:
    if not text:
        return None
    low = text.lower()
    for frag, canonical in index.items():
        if frag in low:
            return canonical
    return None


def _district_for_point(lat: float, lon: float) -> Optional[str]:
    """Resolve an Almaty district by point-in-polygon.

    Deferred import: ``geo.districts`` holds the polygons and the
    point-in-polygon logic lives in ``app._point_in_polygon``. If the Flask
    app module is unavailable (e.g. running from a test), fall back to a
    local ray-casting implementation so enrichment still works.
    """
    try:
        from geo.districts import DISTRICTS
    except Exception:
        return None
    for d in DISTRICTS:
        poly = d.get("polygon") or []
        if poly and _point_in_polygon(lat, lon, poly):
            return d.get("name")
    return None


def _point_in_polygon(lat: float, lon: float,
                      polygon: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon (independent of the Flask app)."""
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i][0], polygon[i][1]
        yj, xj = polygon[j][0], polygon[j][1]
        if ((yi > lat) != (yj > lat)) and \
                (lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside
