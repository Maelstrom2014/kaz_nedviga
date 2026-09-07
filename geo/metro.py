"""Станции метро Алматы (линия 1) и расчёт расстояния до ближайшей.

Координаты — приблизительные центры станций (точность ~100–200 м),
достаточная для отображения расстояния в карточке объявления.
"""
from __future__ import annotations

import math

METRO_STATIONS = [
    {"name": "Бауыржана Момышулы", "lat": 43.2256, "lon": 76.8482},
    {"name": "Сарыарка", "lat": 43.2280, "lon": 76.8578},
    {"name": "Москва", "lat": 43.2305, "lon": 76.8680},
    {"name": "Сайран", "lat": 43.2354, "lon": 76.8779},
    {"name": "Алатау", "lat": 43.2407, "lon": 76.8906},
    {"name": "Театр имени М. Ауэзова", "lat": 43.2455, "lon": 76.9027},
    {"name": "Байконау", "lat": 43.2487, "lon": 76.9118},
    {"name": "Абай", "lat": 43.2540, "lon": 76.9247},
    {"name": "Алмалы", "lat": 43.2598, "lon": 76.9250},
    {"name": "Жибек жолы", "lat": 43.2608, "lon": 76.9360},
    {"name": "Райымбек батыра", "lat": 43.2611, "lon": 76.9455},
]

_EARTH_R = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние между двумя точками в метрах (формула гаверсинусов)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_R * math.asin(math.sqrt(a))


def nearest_metro(lat: float, lon: float) -> tuple[dict | None, float | None]:
    """Ближайшая станция: (station_dict, distance_m) или (None, None)."""
    best: tuple[dict, float] | None = None
    for s in METRO_STATIONS:
        d = haversine_m(lat, lon, s["lat"], s["lon"])
        if best is None or d < best[1]:
            best = (s, d)
    return best if best else (None, None)


def format_distance(m: float) -> str:
    """Человекочитаемое расстояние: «850 м» / «1,2 км»."""
    if m < 1000:
        return f"{round(m / 10) * 10} м"
    return f"{m / 1000:.1f}".replace(".", ",") + " км"
