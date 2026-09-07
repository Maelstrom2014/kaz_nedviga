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
from ml.train_price_model import DEFAULT_CSV, to_float  # noqa: E402


def predict_rows(predict_fn, pp, meta, rows: list[dict]) -> list[tuple[dict, float, str]]:
    th = meta.get("threshold", 0.5)
    out = []
    feats = [pp.row_vector(r) for r in rows]
    probs = predict_fn(feats) if feats else []
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

    from ml.train_price_model import load_serving_model

    predict_fn, meta = load_serving_model()
    if predict_fn is None:
        print("Модель не найдена. Сначала: python ml/train_price_model.py")
        return 1
    pp = _build_pp(meta)

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

    results = predict_rows(predict_fn, pp, meta, rows)
    good = sum(1 for _, p, _ in results if p >= meta.get("threshold", 0.5) + 0.1)
    print(f"Всего: {len(results)}, «хорошая цена»: {good} ({good / max(len(results), 1):.0%})")
    results.sort(key=lambda x: -x[1])
    for r, p, verdict in results[:args.top]:
        title = (r.get("title") or "")[:60]
        price = to_float(r.get("price"))
        print(f"  {p:5.2f} {verdict:<13} "
              f"{price:,.0f} тг  {title}".replace(",", " ") if price else
              f"  {p:5.2f} {verdict:<13} {'—':>12}  {title}")
    return 0


def _build_pp(meta):
    from ml.train_price_model import _pp_from_meta

    return _pp_from_meta(meta)


if __name__ == "__main__":
    sys.exit(main())
