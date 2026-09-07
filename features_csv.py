"""Формирование общего .csv с признаками всех объявлений.

Источники: data/last_results.json (кэш результатов поиска) и/или
избранное из SQLite (db.list_favorites). По каждому объявлению
``features.feature_row`` строит плоскую строку признаков.

Запуск:
    python features_csv.py                     # из кэша, data/features.csv
    python features_csv.py --favorites         # + избранное из БД
    python features_csv.py --out ml/ds.csv     # другой путь
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from features import FEATURE_COLUMNS, build_rows

ROOT = Path(__file__).parent
RESULTS_CACHE = ROOT / "data" / "last_results.json"
DEFAULT_OUT = ROOT / "data" / "features.csv"


def load_results_cache() -> list[dict]:
    """Объявления из кэша последнего поиска (data/last_results.json)."""
    if not RESULTS_CACHE.exists():
        return []
    try:
        data = json.loads(RESULTS_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [r for r in data.values() if isinstance(r, dict)]


def load_favorites() -> list[dict]:
    """Избранные объявления из SQLite (таблица favorites)."""
    try:
        from db import list_favorites
    except Exception:
        return []
    return list_favorites()


def write_csv(rows: list[dict], path: Path) -> int:
    if not rows:
        return 0
    # Порядок: канонический список, затем прочие ключи (union) по алфавиту.
    extra = sorted(
        {k for r in rows for k in r} - set(FEATURE_COLUMNS) - {"row_key", "url", "title"}
    )
    columns = FEATURE_COLUMNS + ["tag_list", "row_key", "url", "title"] + extra
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Общий CSV с признаками объявлений")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="путь к выходному csv")
    ap.add_argument("--favorites", action="store_true", help="включить избранное из БД")
    ap.add_argument("--only-favorites", action="store_true", help="только избранное")
    ap.add_argument("--min-price", type=float, default=0, help="отбросить цену ниже")
    ap.add_argument("--with-price-only", action="store_true",
                    help="только объявления с ценой (нужно для ML)")
    args = ap.parse_args()

    items = [] if args.only_favorites else load_results_cache()
    if args.favorites or args.only_favorites:
        items += load_favorites()
    if not items:
        print("Нет данных: data/last_results.json пуст и/или избранного нет.")
        return 1

    rows = build_rows(items)
    if args.min_price:
        rows = [r for r in rows
                if (r.get("price") or 0) >= args.min_price]
    if args.with_price_only:
        rows = [r for r in rows if r.get("price")]
    if not rows:
        print("После фильтров не осталось строк.")
        return 1

    out = Path(args.out)
    n = write_csv(rows, out)
    filled = sum(1 for r in rows if r.get("price"))
    print(f"OK: {n} строк -> {out} (с ценой: {filled})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
