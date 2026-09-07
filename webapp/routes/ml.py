"""ML-маршруты: обучение модели «хорошая цена или нет» и её статус.

POST /api/ml/train  — запускает переобучение в фоновом потоке (кнопка
«Обучить модель» в настройках). Обновляет data/features.csv (кэш
результатов + избранное) и перезаписывает ml/price_model.pt.
GET  /api/ml/status — статус обучения + метрики последней модели.
"""
from __future__ import annotations

import argparse
import threading
import time

from flask import Blueprint, jsonify

from .. import core

bp = Blueprint("ml", __name__)

_state: dict = {
    "status": "idle",          # idle | running | done | error
    "started_at": None,
    "finished_at": None,
    "metrics": None,
    "error": None,
    "n_rows": None,
}
_lock = threading.Lock()


def _reset_state(status: str) -> None:
    _state.update(status=status, started_at=time.time(), finished_at=None,
                  metrics=None, error=None, n_rows=None)


def _set_error(msg: str) -> None:
    _state.update(status="error", error=msg, finished_at=time.time())
    core.log.warning("[ml] train failed: %s", msg)


def _train_job() -> None:
    """Собрать датасет из кэша+избранного и обучить модель."""
    try:
        from features import build_rows
        from features_csv import load_favorites, load_results_cache, write_csv
        from features_csv import DEFAULT_OUT

        items = load_results_cache() + load_favorites()
        rows = [r for r in build_rows(items) if r.get("price")]
        _state["n_rows"] = len(rows)
        if len(rows) < 20:
            _set_error(
                f"Мало данных: {len(rows)} объявлений с ценой (нужно >=20). "
                "Сделайте поиск, чтобы собрать больше.")
            return
        n = write_csv(rows, DEFAULT_OUT)

        from ml import train_price_model as t

        args = argparse.Namespace(csv=str(DEFAULT_OUT), epochs=600,
                                  patience=60, seed=42)
        rc = t.train(args)
        if rc != 0:
            _set_error(f"Обучение завершилось с кодом {rc}")
            return
        from ml.scorer import model_info

        info = model_info()
        _state.update(status="done", metrics=info["metrics"],
                      finished_at=time.time(), error=None,
                      n_rows=info.get("n_rows") or n)
        core.log.info("[ml] train done: %s rows, metrics=%s",
                      _state["n_rows"], info["metrics"])
    except ImportError as exc:
        _set_error(f"Не установлены зависимости ML (pip install torch): {exc}")
    except Exception as exc:
        core.log.exception("[ml] train crashed")
        _set_error(str(exc))


def _launch_training() -> None:
    threading.Thread(target=_train_job, daemon=True,
                     name="ml-train").start()


@bp.route("/api/ml/train", methods=["POST"])
def api_ml_train():
    with _lock:
        if _state["status"] == "running":
            return jsonify({"status": "running",
                            "started_at": _state["started_at"]}), 409
        _reset_state("running")
    _launch_training()
    return jsonify({"status": "running", "started_at": _state["started_at"]})


@bp.route("/api/ml/status")
def api_ml_status():
    payload = {
        "status": _state["status"],
        "started_at": _state["started_at"],
        "finished_at": _state["finished_at"],
        "metrics": _state["metrics"],
        "error": _state["error"],
        "n_rows": _state["n_rows"],
        "model": None,
    }
    try:
        from ml.scorer import model_info

        payload["model"] = model_info()
    except Exception:
        pass
    return jsonify(payload)
