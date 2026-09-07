"""Скоринг объявлений обученной моделью «хорошая цена или нет».

Модель обучается скриптом ``ml/train_price_model.py`` или кнопкой
«Обучить модель» в настройках. Здесь — ленивая загрузка с авто-reload
при изменении файлов, чтобы после переобучения не требовался перезапуск.
Поддерживаются обе модели из meta.model_type: sklearn_histgb и torch_mlp.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from features import feature_row
from ml.train_price_model import HISTGB_PATH, META_PATH, MODEL_PATH

log = logging.getLogger("app")

_CACHE: dict = {"key": None, "predict": None, "pp": None, "threshold": 0.5}


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _verdict(prob: float, threshold: float) -> tuple[bool, str]:
    if prob >= threshold + 0.1:
        return True, "хорошая цена"
    if prob <= threshold - 0.1:
        return False, "дорого"
    return False, "спорно"


def _load():
    """Лениво загрузить модель; перезагрузить при обновлении файлов.

    Возвращает (predict_batch, pp, threshold) или (None, None, None).
    """
    key = (_mtime(HISTGB_PATH), _mtime(MODEL_PATH), _mtime(META_PATH))
    if _CACHE["predict"] is not None and _CACHE["key"] == key:
        return _CACHE["predict"], _CACHE["pp"], _CACHE["threshold"]
    if not META_PATH.exists() or (key[0] == 0.0 and key[1] == 0.0):
        _CACHE.update(key=key, predict=None, pp=None)
        return None, None, None
    try:
        from ml.train_price_model import _pp_from_meta, load_serving_model

        predict, meta = load_serving_model()
        if predict is None:
            _CACHE.update(key=key, predict=None, pp=None)
            return None, None, None
        pp = _pp_from_meta(meta)
        threshold = meta.get("threshold", 0.5)
        _CACHE.update(key=key, predict=predict, pp=pp, threshold=threshold)
        log.info("[ml] price model loaded (%s, trained on %s rows)",
                 meta.get("model_type"), meta.get("n_rows"))
        return predict, pp, threshold
    except Exception as exc:
        log.warning("[ml] price model load failed: %s", exc)
        _CACHE.update(key=key, predict=None, pp=None)
        return None, None, None


def model_info() -> dict:
    """Состояние модели для UI: есть ли файлы и когда обучалась."""
    info = {
        "available": bool(META_PATH.exists()
                          and (MODEL_PATH.exists() or HISTGB_PATH.exists())),
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
    predict, pp, th = _load()
    if predict is None or not rows:
        return [None] * len(rows)
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
        probs = predict(feats)
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
