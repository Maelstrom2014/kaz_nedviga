"""Обучение нейросети, предсказывающей «хорошая ли цена» у объявления.

Данные: CSV с признаками (см. features_csv.py → data/features.csv).
Разметка автоматическая: объявление считается «хорошей ценой» (label=1),
если его цена не выше медианной цены похожих объявлений (одинаковые
комнаты, тот же район, близкая площадь). Модель: PyTorch MLP.

Запуск:
    python ml/train_price_model.py                      # data/features.csv
    python ml/train_price_model.py --csv ml/ds.csv --epochs 400

Артефакты: ml/price_model.pt, ml/price_meta.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from features import CATEGORICAL_FEATURES, NUMERIC_FEATURES  # noqa: E402

DEFAULT_CSV = ROOT / "data" / "features.csv"
MODEL_PATH = Path(__file__).parent / "price_model.pt"
META_PATH = Path(__file__).parent / "price_meta.json"

MISSING = ""  # значение "нет данных" в CSV


def load_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if (r.get("price") or "").strip()]


def to_float(v) -> float | None:
    try:
        x = float(str(v).replace(",", ".").replace(" ", ""))
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def area_bucket(area: float | None) -> str:
    if area is None or area <= 0:
        return "?"
    step = 15.0  # корзины 15 м²: 0-15, 15-30, ...
    return str(int((area - 1) // step))


def auto_label(rows: list[dict], ratio: float = 1.0, min_group: int = 3) -> list[int]:
    """1 = «хорошая цена»: не дороже медианы похожих объявлений."""
    groups: dict[tuple, list[float]] = {}
    for r in rows:
        key = (
            (r.get("rooms") or MISSING).strip(),
            (r.get("district") or MISSING).strip().lower(),
            area_bucket(to_float(r.get("area"))),
        )
        p = to_float(r.get("price"))
        if p:
            groups.setdefault(key, []).append(p)
    global_median = statistics.median(
        [p for ps in groups.values() for p in ps])
    labels = []
    for r in rows:
        key = (
            (r.get("rooms") or MISSING).strip(),
            (r.get("district") or MISSING).strip().lower(),
            area_bucket(to_float(r.get("area"))),
        )
        ps = groups[key]
        med = statistics.median(ps) if len(ps) >= min_group else global_median
        labels.append(int(to_float(r.get("price")) <= med * ratio))
    return labels


class Preprocessor:
    """Числовые → (median-impute, z-score); категориальные → one-hot."""

    def fit(self, rows: list[dict]) -> "Preprocessor":
        self.medians: dict[str, float] = {}
        self.means: dict[str, float] = {}
        self.stds: dict[str, float] = {}
        for c in NUMERIC_FEATURES:
            vals = [to_float(r.get(c)) for r in rows]
            known = sorted(v for v in vals if v is not None)
            med = statistics.median(known) if known else 0.0
            self.medians[c] = med
            filled = [v if v is not None else med for v in vals]
            mean = statistics.fmean(filled) if filled else 0.0
            var = statistics.fmean([(x - mean) ** 2 for x in filled]) if filled else 0.0
            self.means[c] = mean
            self.stds[c] = math.sqrt(var) or 1.0
        self.vocabs: dict[str, list[str]] = {}
        for c in CATEGORICAL_FEATURES:
            counts: dict[str, int] = {}
            for r in rows:
                v = (r.get(c) or MISSING).strip().lower() or MISSING
                counts[v] = counts.get(v, 0) + 1
            self.vocabs[c] = sorted(
                v for v, n in counts.items() if n >= 2)
        return self

    @property
    def n_numeric(self) -> int:
        return len(NUMERIC_FEATURES)

    @property
    def n_input(self) -> int:
        return self.n_numeric + sum(len(v) for v in self.vocabs.values())

    def row_vector(self, r: dict) -> list[float]:
        vec: list[float] = []
        for c in NUMERIC_FEATURES:
            v = to_float(r.get(c))
            v = v if v is not None else self.medians[c]
            vec.append((v - self.means[c]) / self.stds[c])
        for c in CATEGORICAL_FEATURES:
            vocab = self.vocabs[c]
            v = (r.get(c) or MISSING).strip().lower() or MISSING
            onehot = [0.0] * len(vocab)
            if v in vocab:
                onehot[vocab.index(v)] = 1.0
            vec.extend(onehot)
        return vec


def split_indices(n: int, seed: int, val_frac: float = 0.2):
    import random
    rng = random.Random(seed)
    idx = list(range(n))
    rng.shuffle(idx)
    n_val = max(1, int(n * val_frac))
    return idx[n_val:], idx[:n_val]


def train(args) -> int:
    import torch
    import torch.nn as nn

    torch.manual_seed(args.seed)

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"Нет файла {csv_path}. Сначала: python features_csv.py --with-price-only")
        return 1
    rows = load_rows(csv_path)
    if len(rows) < 20:
        print(f"Мало данных: {len(rows)} строк (нужно >=20). Соберите больше объявлений.")
        return 1
    labels = auto_label(rows)
    pos = sum(labels)
    print(f"Строк: {len(rows)}, «хорошая цена»: {pos} ({pos / len(rows):.0%})")

    pp = Preprocessor().fit(rows)
    X = torch.tensor([pp.row_vector(r) for r in rows], dtype=torch.float32)
    y = torch.tensor(labels, dtype=torch.float32).unsqueeze(1)

    train_i, val_i = split_indices(len(rows), args.seed)
    X_tr, y_tr = X[train_i], y[train_i]
    X_va, y_va = X[val_i], y[val_i]

    model = nn.Sequential(
        nn.Linear(pp.n_input, 64), nn.ReLU(), nn.Dropout(0.2),
        nn.Linear(64, 32), nn.ReLU(), nn.Dropout(0.2),
        nn.Linear(32, 1),
    )
    pos_weight = torch.tensor(
        [(len(labels) - pos) / max(pos, 1)], dtype=torch.float32)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)

    best_val, best_state, patience = float("inf"), None, 0
    for epoch in range(args.epochs):
        model.train()
        opt.zero_grad()
        loss = loss_fn(model(X_tr), y_tr)
        loss.backward()
        opt.step()
        model.eval()
        with torch.no_grad():
            va_logits = model(X_va)
            val_loss = float(loss_fn(va_logits, y_va))
        if val_loss < best_val - 1e-4:
            best_val, best_state, patience = val_loss, {
                k: v.clone() for k, v in model.state_dict().items()}, 0
        else:
            patience += 1
            if patience >= args.patience:
                break
        if (epoch + 1) % 50 == 0 or epoch == 0:
            print(f"epoch {epoch + 1:4d}  train_loss={float(loss):.4f}  val_loss={val_loss:.4f}")

    if best_state is not None:
        model.load_state_dict(best_state)

    metrics = evaluate(model, X_va, y_va)
    print(f"Val: acc={metrics['accuracy']:.3f} precision={metrics['precision']:.3f} "
          f"recall={metrics['recall']:.3f} f1={metrics['f1']:.3f} auc={metrics['auc']:.3f}")

    torch.save(model.state_dict(), MODEL_PATH)
    META_PATH.write_text(json.dumps({
        "numeric_features": NUMERIC_FEATURES,
        "categorical_features": CATEGORICAL_FEATURES,
        "medians": pp.medians, "means": pp.means, "stds": pp.stds,
        "vocabs": pp.vocabs, "n_input": pp.n_input,
        "label_rule": "price <= median(rooms, district, area_bucket)",
        "threshold": 0.5, "seed": args.seed, "metrics": metrics,
        "n_rows": len(rows),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: {MODEL_PATH}\n    {META_PATH}")
    return 0


def evaluate(model, X, y) -> dict:
    import torch

    model.eval()
    with torch.no_grad():
        p = torch.sigmoid(model(X)).squeeze(1)
    y = y.squeeze(1)
    pred = (p >= 0.5).float()
    tp = float(((pred == 1) & (y == 1)).sum())
    fp = float(((pred == 1) & (y == 0)).sum())
    fn = float(((pred == 0) & (y == 1)).sum())
    tn = float(((pred == 0) & (y == 0)).sum())
    acc = (tp + tn) / max(len(y), 1)
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-9)
    # AUC (rank-based)
    order = p.argsort()
    ranks = torch.empty_like(p)
    ranks[order] = torch.arange(1, len(p) + 1, dtype=torch.float32)
    n_pos = float(y.sum())
    n_neg = float(len(y) - n_pos)
    auc = (ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2) / max(n_pos * n_neg, 1) if n_pos and n_neg else 0.5
    return {"accuracy": round(acc, 4), "precision": round(precision, 4),
            "recall": round(recall, 4), "f1": round(f1, 4), "auc": round(float(auc), 4)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Обучение модели «хорошая цена или нет»")
    ap.add_argument("--csv", default=str(DEFAULT_CSV))
    ap.add_argument("--epochs", type=int, default=600)
    ap.add_argument("--patience", type=int, default=60)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    try:
        import torch  # noqa: F401
    except ImportError:
        print("Нужен torch: pip install torch")
        return 1
    return train(args)


if __name__ == "__main__":
    sys.exit(main())
