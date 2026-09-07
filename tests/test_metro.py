"""Тесты расчёта расстояния до метро и аннотации карточек."""
from __future__ import annotations

import json

import pytest

from app import app
from geo.metro import format_distance, nearest_metro


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("webapp.core._RESULTS_CACHE", tmp_path / "last_results.json")
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_nearest_metro_near_station():
    """Точка у ст. Сайран → Сайран, погрешность < 300 м."""
    station, dist = nearest_metro(43.2354, 76.8779)
    assert station["name"] == "Сайран"
    assert dist < 300


def test_nearest_metro_center():
    """Центр города (ул. Панфилова/Гоголя) → Жибек жолы или Алмалы."""
    station, dist = nearest_metro(43.2600, 76.9300)
    assert station["name"] in ("Жибек жолы", "Алмалы")
    assert dist < 800


def test_nearest_metro_far_southwest():
    station, dist = nearest_metro(43.2100, 76.8300)
    assert station["name"] == "Бауыржана Момышулы"
    assert 1500 < dist < 4000


def test_format_distance():
    assert format_distance(852) == "850 м"
    assert format_distance(1234) == "1,2 км"
    assert format_distance(1000) == "1,0 км"


def test_results_annotated_with_metro(client, tmp_path):
    """/api/results добавляет metro{name, distance_m} при наличии координат."""
    payload = {"k1": {"title": "1-к", "price": 150000, "source": "krisha",
                      "url": "https://x/1", "rooms": 1, "area": 30.0,
                      "currency": "тг", "photo": "http://x/1.jpg",
                      "lat": 43.2598, "lon": 76.9250}}
    tmp_path.joinpath("last_results.json").write_text(
        json.dumps(payload), encoding="utf-8")
    results = client.get("/api/results").get_json()["results"]
    metro = results[0].get("metro")
    assert metro and metro["name"] == "Алмалы"
    assert metro["distance_m"] < 300
