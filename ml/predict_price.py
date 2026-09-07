"""Предсказание «хорошая ли цена» для CSV с признаками или JSON объявления.

Запуск:
    python ml/predict_price.py                       # data/features.csv
    python ml/predict_price.py --csv ml/ds.csv
    python ml/predict_price.py --json listing.json   # одно объявление
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from features import feature_row  # noqa: E402
from ml.train_price_model import (  # noqa: E402
    DEFAULT_CSV, META_PATH, MODEL_PATH, Preprocessor, to_float,
)

DEFAULT_MODEL = MODEL_PATH


def load_model():
    import torch

    if not MODEL_PATH.exists() or not META_PATH.exists():
        print("Модель не найдена. Сначала: python ml/train_price_model.py")
        return None, None
    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    pp = Preprocessor()
    pp.medians = meta["medians"]
    pp.means = meta["means"]
    pp.stds = meta["stds"]
    pp.vocabs = meta["vocabs"]
    if pp.n_input != meta["n_input"]:
        print("Размерность модели не совпадает с метаданными — переобучите модель.")
        return None, None, None
    import torch.nn as nn

    model = nn.Sequential(
        nn.Linear(meta["n_input"], 64), nn.ReLU(), nn.Dropout(0.0),
        nn.Linear(64, 32), nn.ReLU(), nn.Dropout(0.0),
        nn.Linear(32, 1),
    )
    model.load_state_dict(torch.load(MODEL_PATH, map_location="cpu"))
    model.eval()
    return model, meta, pp


def predict_rows(model, meta, pp: Preprocessor, rows: list[dict]) -> list[tuple[dict, float, str]]:
    import torch

    th = meta.get("threshold", 0.5)
    out = []
    with torch.no_grad():
        X = torch.tensor([pp.row_vector(r) for r in rows],
                         dtype=torch.float32)
        probs = torch.sigmoid(model(X)).squeeze(1).tolist()
    for r, p in zip(rows, probs):
        if p >= th + 0.1:
            verdict = "хорошая цена"
        elif p <= th - 0.1:
            verdict = "дорого"
        else:
            verdict = "спорно"
        out.append((r, p, verdict))
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description="Оценка «хорошая цена или нет»")
    ap.add_argument("--csv", default=str(DEFAULT_CSV))
    ap.add_argument("--json", help="файл JSON с одним объявлением (dict)")
    ap.add_argument("--top", type=int, default=15, help="сколько строк показать")
    args = ap.parse_args()

    model, meta, pp = load_model()
    if model is None:
        return 1

    if args.json:
        with open(args.json, encoding="utf-8") as f:
            items = [json.load(f)]
        rows = [feature_row(it) for it in items]
    else:
        import csv

        p = Path(args.csv)
        if not p.exists():
            print(f"Нет файла {p}. Сначала: python features_csv.py")
            return 1
        with p.open(newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        rows = [r for r in rows if (r.get("price") or "").strip()]

    results = predict_rows(model, meta, pp, rows)
    good = sum(1 for _, p, _ in results if p >= 0.6)
    print(f"Всего: {len(results)}, «хорошая цена»: {good} ({good / max(len(results), 1):.0%})")
    results.sort(key=lambda x: -x[1])
    for r, p, verdict in results[:args.top]:
        title = (r.get("title") or "")[:60]
        print(f"  {p:5.2f} {verdict:<13} {to_float(r.get('price')):>10.0f} тг  {title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
