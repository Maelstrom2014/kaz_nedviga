"""Скоринг объявлений обученной моделью «хорошая цена или нет».

Модель (ml/price_model.pt + ml/price_meta.json) обучается скриптом
``ml/train_price_model.py`` или кнопкой «Обучить модель» в настройках.
Здесь — ленивая загрузка с авто-reload при изменении файла метаданных,
чтобы после переобучения не требовался перезапуск сервера.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from features import feature_row
from ml.train_price_model import META_PATH, MODEL_PATH, Preprocessor

log = logging.getLogger("app")

_CACHE: dict = {"mtime": None, "model": None, "pp": None, "threshold": 0.5}


def _verdict(prob: float, threshold: float) -> tuple[bool, str]:
    if prob >= threshold + 0.1:
        return True, "хорошая цена"
    if prob <= threshold - 0.1:
        return False, "дорого"
    return False, "спорно"


def _load():
    """Лениво загрузить модель; перезагрузить при обновлении файлов."""
    if not (MODEL_PATH.exists() and META_PATH.exists()):
        _CACHE.update(mtime=None, model=None, pp=None)
        return None, None
    mtime = (MODEL_PATH.stat().st_mtime, META_PATH.stat().st_mtime)
    if _CACHE["model"] is not None and _CACHE["mtime"] == mtime:
        return _CACHE["model"], _CACHE["pp"]
    try:
        import torch
        import torch.nn as nn

        meta = json.loads(META_PATH.read_text(encoding="utf-8"))
        pp = Preprocessor()
        pp.medians = meta["medians"]
        pp.means = meta["means"]
        pp.stds = meta["stds"]
        pp.vocabs = meta["vocabs"]
        if pp.n_input != meta["n_input"]:
            log.warning("[ml] meta n_input mismatch — model skipped")
            return None, None
        model = nn.Sequential(
            nn.Linear(meta["n_input"], 64), nn.ReLU(), nn.Dropout(0.0),
            nn.Linear(64, 32), nn.ReLU(), nn.Dropout(0.0),
            nn.Linear(32, 1),
        )
        model.load_state_dict(torch.load(MODEL_PATH, map_location="cpu"))
        model.eval()
        _CACHE.update(mtime=mtime, model=model, pp=pp,
                      threshold=meta.get("threshold", 0.5))
        log.info("[ml] price model loaded (trained on %s rows)",
                 meta.get("n_rows"))
        return model, pp
    except Exception as exc:
        log.warning("[ml] price model load failed: %s", exc)
        _CACHE.update(mtime=None, model=None, pp=None)
        return None, None


def model_info() -> dict:
    """Состояние модели для UI: есть ли файлы и когда обучалась."""
    info = {
        "available": bool(MODEL_PATH.exists() and META_PATH.exists()),
        "metrics": None, "n_rows": None, "trained_at": None,
    }
    if info["available"]:
        try:
            meta = json.loads(META_PATH.read_text(encoding="utf-8"))
            info["metrics"] = meta.get("metrics")
            info["n_rows"] = meta.get("n_rows")
            info["trained_at"] = META_PATH.stat().st_mtime
        except Exception:
            pass
    return info


def score_rows(rows: list[dict]) -> list[dict | None]:
    """Вероятность «хорошей цены» для каждой строки (Listing-дикт).

    Возвращает список той же длины: dict или None (нет модели/цены).
    """
    model, pp = _load()
    if model is None or not rows:
        return [None] * len(rows)
    th = _CACHE["threshold"]
    scored: list[dict | None] = [None] * len(rows)
    vec_rows: list[tuple[int, dict]] = []
    feats: list[list[float]] = []
    for i, r in enumerate(rows):
        if not (r or {}).get("price"):
            continue
        try:
            f = feature_row(r)
        except Exception:
            continue
        vec_rows.append((i, f))
        feats.append(pp.row_vector(f))
    if vec_rows:
        import torch

        with torch.no_grad():
            X = torch.tensor(feats, dtype=torch.float32)
            probs = torch.sigmoid(model(X)).squeeze(1).tolist()
        for (i, f), p in zip(vec_rows, probs):
            good, verdict = _verdict(float(p), th)
            scored[i] = {
                "prob": round(float(p), 3), "good": good,
                "verdict": verdict, "price_per_m2": f.get("price_per_m2"),
            }
    return scored


def annotate_rows(rows: list[dict]) -> None:
    """Добавить ``price_eval`` в каждый дикт результата (in place)."""
    try:
        for r, ev in zip(rows, score_rows(rows)):
            if ev is not None:
                r["price_eval"] = ev
    except Exception as exc:  # никогда не ломаем выдачу поиска
        log.warning("[ml] scoring failed: %s", exc)
