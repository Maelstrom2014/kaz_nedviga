"""Тесты ML-маршрутов (/api/ml/train, /api/ml/status) и скоринга карточек."""
from __future__ import annotations

import json
import time

import pytest

from app import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("webapp.core._RESULTS_CACHE", tmp_path / "last_results.json")
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def _reset_state():
    from webapp.routes import ml

    ml._state.update(status="idle", started_at=None, finished_at=None,
                     metrics=None, error=None, n_rows=None)
    yield ml._state
    ml._state.update(status="idle", started_at=None, finished_at=None,
                     metrics=None, error=None, n_rows=None)


def test_ml_status_idle(client, _reset_state):
    resp = client.get("/api/ml/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "idle"
    assert "model" in data


def test_ml_train_starts_and_finishes(client, _reset_state, monkeypatch):
    from webapp.routes import ml

    def fake_job():
        ml._state.update(status="done", metrics={"f1": 0.7, "auc": 0.8},
                         finished_at=time.time(), n_rows=42)
    monkeypatch.setattr(ml, "_train_job", fake_job)
    resp = client.post("/api/ml/train")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "running"

    for _ in range(50):
        data = client.get("/api/ml/status").get_json()
        if data["status"] != "running":
            break
        time.sleep(0.05)
    data = client.get("/api/ml/status").get_json()
    assert data["status"] == "done"
    assert data["n_rows"] == 42
    assert data["metrics"]["auc"] == 0.8


def test_ml_train_conflict_while_running(client, _reset_state):
    from webapp.routes import ml

    ml._state["status"] = "running"
    resp = client.post("/api/ml/train")
    assert resp.status_code == 409
    assert resp.get_json()["status"] == "running"


def test_ml_train_error_reported(client, _reset_state, monkeypatch):
    from webapp.routes import ml

    def bad_job():
        ml._set_error("torch не установлен")
    monkeypatch.setattr(ml, "_train_job", bad_job)
    client.post("/api/ml/train")
    for _ in range(50):
        data = client.get("/api/ml/status").get_json()
        if data["status"] != "running":
            break
        time.sleep(0.05)
    data = client.get("/api/ml/status").get_json()
    assert data["status"] == "error"
    assert "torch" in data["error"]


def test_results_annotated_with_price_eval(client, monkeypatch, tmp_path):
    """Если модель доступна — /api/results добавляет price_eval."""
    def mk_listing():
        return {"title": "1-к", "price": 150000, "source": "krisha",
                "url": "https://x/1", "rooms": 1, "area": 30.0,
                "currency": "тг", "photo": "http://x/1.jpg"}

    payload = {"k1": {**mk_listing(), "listing_key": "k1"}}
    tmp_path.joinpath("last_results.json").write_text(
        json.dumps(payload), encoding="utf-8")
    calls = []

    def fake_annotate(rows):
        calls.append(len(rows))
        for r in rows:
            r["price_eval"] = {"prob": 0.9, "good": True,
                               "verdict": "хорошая цена"}
    monkeypatch.setattr("ml.scorer.annotate_rows", fake_annotate)
    resp = client.get("/api/results")
    assert resp.status_code == 200
    results = resp.get_json()["results"]
    assert results and results[0]["price_eval"]["good"] is True
    assert calls == [len(results)]


def test_results_without_model_unchanged(client, monkeypatch, tmp_path):
    """Без модели annotate_rows — no-op и не ломает выдачу."""
    payload = {"k1": {"title": "1-к", "price": 150000, "source": "krisha",
                      "url": "https://x/1", "rooms": 1, "area": 30.0,
                      "listing_key": "k1", "currency": "тг",
                      "photo": "http://x/1.jpg"}}
    tmp_path.joinpath("last_results.json").write_text(
        json.dumps(payload), encoding="utf-8")

    def raising_annotate(rows):
        raise RuntimeError("no model")
    monkeypatch.setattr("ml.scorer.annotate_rows", raising_annotate)
    resp = client.get("/api/results")
    assert resp.status_code == 200
    assert "price_eval" not in resp.get_json()["results"][0]
