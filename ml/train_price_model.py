"""Обучение модели, предсказывающей «хорошая ли цена» у объявления.

Данные: CSV с признаками (см. features_csv.py → data/features.csv).
Разметка автоматическая:
  - median   (по умолчанию исторически): цена ≤ медианы похожих
  - quantile (продакшн): цена (или цена/м²) в нижних 35% своей группы —
    «дешевле 70% похожих», метка реже и осмысленнее

Модели:
  - histgb (по умолчанию, победитель OOF-бенчмарка ml/experiments.py):
    HistGradientBoostingClassifier — стабильно лучше MLP на табличных
    данных такого объёма
  - mlp: PyTorch MLP (оставлен для сравнения)

Важно: цена и её производные ИСКЛЮЧЕНЫ из признаков (PRICE_FEATURES) —
метка строится из цены, и модель, видя price, переучивает правило
разметки (утечка: AUC ~0.99 вместо честных ~0.6).

Запуск:
    python ml/train_price_model.py
    python ml/train_price_model.py --model mlp --labels median

Артефакты: ml/price_model.pkl (histgb) | ml/price_model.pt (mlp),
ml/price_meta.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import pickle
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from features import CATEGORICAL_FEATURES, NUMERIC_FEATURES  # noqa: E402

DEFAULT_CSV = ROOT / "data" / "features.csv"
MODEL_PATH = Path(__file__).parent / "price_model.pt"
HISTGB_PATH = Path(__file__).parent / "price_model.pkl"
META_PATH = Path(__file__).parent / "price_meta.json"

MISSING = ""  # значение "нет данных" в CSV

PRICE_FEATURES = {"price", "price_per_m2", "price_per_room"}
MODEL_NUMERIC = [c for c in NUMERIC_FEATURES if c not in PRICE_FEATURES]


def load_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    rows = [r for r in rows if (r.get("price") or "").strip()]
    # Дедупликация: избранное дублирует строки кэша (тот же source|url),
    # а дубль в train+val одновременно завышает/занижает метрики случайно.
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for r in rows:
        key = ((r.get("source") or "").strip().lower(),
               (r.get("url") or "").strip())
        if key != ("", "") and key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def to_float(v) -> float | None:
    try:
        x = float(str(v).strip().replace(" ", "").replace(",", "."))
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def area_bucket(area: float | None) -> str:
    if area is None or area <= 0:
        return "?"
    step = 15.0  # корзины 15 м²: 0-15, 15-30, ...
    return str(int((area - 1) // step))


# ---------- разметка ----------

def auto_label(rows: list[dict], ratio: float = 1.0, min_group: int = 3) -> list[int]:
    """median: 1 = «хорошая цена»: не дороже медианы похожих объявлений."""
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


def labels_quantile(rows: list[dict], q: float = 0.35) -> list[int]:
    """quantile: 1 = цена в нижних q своей группы.

    С площадью: цена/м², группа (rooms, area_bucket) — убирает смещение
    «большая квартира = дорого». Без площади: цена, группа
    (rooms, district). Порог = q-перцентиль группы (мин. 10 значений,
    иначе глобальный).
    """
    by_m2: dict[tuple, list[float]] = {}
    by_price: dict[tuple, list[float]] = {}
    for r in rows:
        p, a = to_float(r.get("price")), to_float(r.get("area"))
        rooms = (r.get("rooms") or MISSING).strip()
        if p and a:
            by_m2.setdefault((rooms, area_bucket(a)), []).append(p / a)
        elif p:
            by_price.setdefault(
                (rooms, (r.get("district") or MISSING).strip().lower()), []
            ).append(p)

    def _thr(values: list[float], fallback: list[float]) -> float:
        pool = values if len(values) >= 10 else fallback
        return (statistics.quantiles(pool, n=10)[int(q * 10) - 1]
                if len(pool) >= 20 else 0.0)

    all_m2 = [v for vs in by_m2.values() for v in vs]
    all_price = [v for vs in by_price.values() for v in vs]
    g_m2 = _thr([], all_m2)
    g_price = _thr([], all_price)

    labels = []
    for r in rows:
        p, a = to_float(r.get("price")), to_float(r.get("area"))
        rooms = (r.get("rooms") or MISSING).strip()
        if p and a:
            thr = _thr(by_m2[(rooms, area_bucket(a))], [g_m2])
            labels.append(int(p / a <= thr))
        elif p:
            thr = _thr(
                by_price[(rooms, (r.get("district") or MISSING).strip().lower())],
                [g_price])
            labels.append(int(p <= thr))
        else:
            labels.append(0)
    return labels


class Preprocessor:
    """Числовые → (median-impute, z-score); категориальные → one-hot."""

    def __init__(self, numeric: list[str] | None = None,
                 categorical: list[str] | None = None):
        self.numeric = numeric if numeric is not None else list(MODEL_NUMERIC)
        self.categorical = (categorical if categorical is not None
                            else list(CATEGORICAL_FEATURES))

    def fit(self, rows: list[dict]) -> "Preprocessor":
        self.medians: dict[str, float] = {}
        self.means: dict[str, float] = {}
        self.stds: dict[str, float] = {}
        for c in self.numeric:
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
        for c in self.categorical:
            counts: dict[str, int] = {}
            for r in rows:
                v = (r.get(c) or MISSING).strip().lower() or MISSING
                counts[v] = counts.get(v, 0) + 1
            self.vocabs[c] = sorted(
                v for v, n in counts.items() if n >= 2)
        return self

    @property
    def n_numeric(self) -> int:
        return len(self.numeric)

    @property
    def n_input(self) -> int:
        return self.n_numeric + sum(len(v) for v in self.vocabs.values())

    def row_vector(self, r: dict) -> list[float]:
        vec: list[float] = []
        for c in self.numeric:
            v = to_float(r.get(c))
            v = v if v is not None else self.medians[c]
            vec.append((v - self.means[c]) / self.stds[c])
        for c in self.categorical:
            vocab = self.vocabs[c]
            v = (r.get(c) or MISSING).strip().lower() or MISSING
            onehot = [0.0] * len(vocab)
            if v in vocab:
                onehot[vocab.index(v)] = 1.0
            vec.extend(onehot)
        return vec


def split_indices(n: int, seed: int, val_frac: float = 0.2,
                  labels: list[int] | None = None):
    """Стратифицированный сплит: доля классов в val = доле в данных."""
    import random
    rng = random.Random(seed)
    if labels is None:
        idx = list(range(n))
        rng.shuffle(idx)
        n_val = max(1, int(n * val_frac))
        return idx[n_val:], idx[:n_val]
    pos = [i for i, l in enumerate(labels) if l == 1]
    neg = [i for i, l in enumerate(labels) if l == 0]
    rng.shuffle(pos)
    rng.shuffle(neg)
    n_val_pos = max(1, int(len(pos) * val_frac)) if pos else 0
    n_val_neg = max(1, int(len(neg) * val_frac)) if neg else 0
    val = pos[:n_val_pos] + neg[:n_val_neg]
    train = pos[n_val_pos:] + neg[n_val_neg:]
    rng.shuffle(train)
    rng.shuffle(val)
    return train, val


def pick_threshold(y: list[int], probs: list[float]) -> tuple[float, float]:
    """Порог, максимизирующий F1 (0.5 для несбалансированных задач неоптимален)."""
    best_t, best_f1 = 0.5, 0.0
    for i in range(5, 96):
        t = i / 100
        pred = [int(v >= t) for v in probs]
        tp = sum(1 for pr, yy in zip(pred, y) if pr and yy)
        fp = sum(1 for pr, yy in zip(pred, y) if pr and not yy)
        fn = sum(1 for pr, yy in zip(pred, y) if not pr and yy)
        precision = tp / max(tp + fp, 1)
        recall = tp / max(tp + fn, 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-9)
        if f1 > best_f1:
            best_f1, best_t = f1, t
    return best_t, best_f1


def eval_lists(y: list[int], probs: list[float], threshold: float) -> dict:
    order = sorted(range(len(probs)), key=lambda i: probs[i])
    ranks = [0] * len(probs)
    for rank, i in enumerate(order, 1):
        ranks[i] = rank
    n_pos = sum(y)
    n_neg = len(y) - n_pos
    auc = ((sum(r for r, yy in zip(ranks, y) if yy) - n_pos * (n_pos + 1) / 2)
           / max(n_pos * n_neg, 1)) if n_pos and n_neg else 0.5
    pred = [int(v >= threshold) for v in probs]
    tp = sum(1 for pr, yy in zip(pred, y) if pr and yy)
    fp = sum(1 for pr, yy in zip(pred, y) if pr and not yy)
    fn = sum(1 for pr, yy in zip(pred, y) if not pr and yy)
    tn = len(y) - tp - fp - fn
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-9)
    acc = (tp + tn) / max(len(y), 1)
    return {"accuracy": round(acc, 4), "precision": round(precision, 4),
            "recall": round(recall, 4), "f1": round(f1, 4),
            "auc": round(float(auc), 4)}


def _pp_from_meta(meta: dict) -> Preprocessor:
    pp = Preprocessor(numeric=meta["numeric_features"],
                      categorical=meta["categorical_features"])
    pp.medians = meta["medians"]
    pp.means = meta["means"]
    pp.stds = meta["stds"]
    pp.vocabs = meta["vocabs"]
    return pp


def load_serving_model():
    """Загрузить обученную модель для инференса.

    Возвращает (predict_batch, meta) или (None, None):
    predict_batch(vecs: list[list[float]]) -> list[float] — вероятности.
    """
    if not META_PATH.exists():
        return None, None
    try:
        meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None, None
    model_type = meta.get("model_type", "torch_mlp")
    try:
        if model_type == "sklearn_histgb":
            if not HISTGB_PATH.exists():
                return None, None
            model = pickle.loads(HISTGB_PATH.read_bytes())

            def predict(vecs):
                return [float(p[1]) for p in model.predict_proba(vecs)]
        else:
            import torch
            import torch.nn as nn

            if not MODEL_PATH.exists():
                return None, None
            model = nn.Sequential(
                nn.Linear(meta["n_input"], 64), nn.ReLU(), nn.Dropout(0.0),
                nn.Linear(64, 32), nn.ReLU(), nn.Dropout(0.0),
                nn.Linear(32, 1),
            )
            model.load_state_dict(
                torch.load(MODEL_PATH, map_location="cpu"))
            model.eval()

            def predict(vecs):
                with torch.no_grad():
                    X = torch.tensor(vecs, dtype=torch.float32)
                    return torch.sigmoid(model(X)).squeeze(1).tolist()
    except Exception:
        return None, None
    return predict, meta


def train(args) -> int:
    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"Нет файла {csv_path}. Сначала: python features_csv.py --with-price-only")
        return 1
    rows = load_rows(csv_path)
    if len(rows) < 20:
        print(f"Мало данных: {len(rows)} строк (нужно >=20). Соберите больше объявлений.")
        return 1

    if args.labels == "quantile":
        labels = labels_quantile(rows)
        label_rule = f"price(/m2) in bottom {args.quantile:.0%} of group"
    else:
        labels = auto_label(rows)
        label_rule = "price <= median(rooms, district, area_bucket)"
    pos = sum(labels)
    print(f"Строк: {len(rows)}, «хорошая цена»: {pos} ({pos / len(rows):.0%})")

    pp = Preprocessor(numeric=MODEL_NUMERIC).fit(rows)
    X_all = [pp.row_vector(r) for r in rows]
    train_i, val_i = split_indices(len(rows), args.seed, labels=labels)
    X_tr = [X_all[i] for i in train_i]
    y_tr = [labels[i] for i in train_i]
    X_va = [X_all[i] for i in val_i]
    y_va = [labels[i] for i in val_i]

    if args.model == "histgb":
        from sklearn.ensemble import HistGradientBoostingClassifier

        model = HistGradientBoostingClassifier(
            max_iter=400, learning_rate=0.06, min_samples_leaf=15,
            l2_regularization=1.0, class_weight="balanced",
            early_stopping=True, validation_fraction=0.15,
            random_state=args.seed)
        model.fit(X_tr, y_tr)
        probs_va = model.predict_proba(X_va)[:, 1].tolist()
        threshold, _ = pick_threshold(y_va, probs_va)
        metrics = eval_lists(y_va, probs_va, threshold)
        model_full = HistGradientBoostingClassifier(
            max_iter=400, learning_rate=0.06, min_samples_leaf=15,
            l2_regularization=1.0, class_weight="balanced",
            early_stopping=True, validation_fraction=0.15,
            random_state=args.seed)
        model_full.fit([pp.row_vector(r) for r in rows], labels)
        HISTGB_PATH.write_bytes(pickle.dumps(model_full))
        MODEL_PATH.unlink(missing_ok=True)
        model_type = "sklearn_histgb"
    else:
        import torch

        torch.manual_seed(args.seed)
        threshold, metrics = _train_mlp(X_tr, y_tr, X_va, y_va, args)
        HISTGB_PATH.unlink(missing_ok=True)
        model_type = "torch_mlp"

    print(f"Val: acc={metrics['accuracy']:.3f} precision={metrics['precision']:.3f} "
          f"recall={metrics['recall']:.3f} f1={metrics['f1']:.3f} auc={metrics['auc']:.3f} "
          f"(порог F1={threshold:.2f})")

    meta = {
        "model_type": model_type,
        "numeric_features": MODEL_NUMERIC,
        "categorical_features": CATEGORICAL_FEATURES,
        "medians": pp.medians, "means": pp.means, "stds": pp.stds,
        "vocabs": pp.vocabs, "n_input": pp.n_input,
        "label_rule": label_rule,
        "threshold": threshold, "seed": args.seed, "metrics": metrics,
        "n_rows": len(rows),
    }
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    print(f"OK: {HISTGB_PATH if model_type == 'sklearn_histgb' else MODEL_PATH}\n"
          f"    {META_PATH}")
    return 0


def _train_mlp(X_tr, y_tr, X_va, y_va, args) -> tuple[float, dict]:
    """Обучение PyTorch MLP; возвращает (threshold, metrics)."""
    import torch
    import torch.nn as nn

    torch.manual_seed(args.seed)
    X_tr_t = torch.tensor(X_tr, dtype=torch.float32)
    y_tr_t = torch.tensor(y_tr, dtype=torch.float32).unsqueeze(1)
    X_va_t = torch.tensor(X_va, dtype=torch.float32)
    y_va_t = torch.tensor(y_va, dtype=torch.float32).unsqueeze(1)

    model = nn.Sequential(
        nn.Linear(len(X_tr[0]), 64), nn.ReLU(), nn.Dropout(0.2),
        nn.Linear(64, 32), nn.ReLU(), nn.Dropout(0.2),
        nn.Linear(32, 1),
    )
    pos_weight = torch.tensor(
        [y_tr.count(0) / max(y_tr.count(1), 1)], dtype=torch.float32)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)

    best_val, best_state, patience = float("inf"), None, 0
    for epoch in range(args.epochs):
        model.train()
        opt.zero_grad()
        loss = loss_fn(model(X_tr_t), y_tr_t)
        loss.backward()
        opt.step()
        model.eval()
        with torch.no_grad():
            val_loss = float(loss_fn(model(X_va_t), y_va_t))
        if val_loss < best_val - 1e-4:
            best_val, best_state, patience = val_loss, {
                k: v.clone() for k, v in model.state_dict().items()}, 0
        else:
            patience += 1
            if patience >= args.patience:
                break
        if (epoch + 1) % 50 == 0 or epoch == 0:
            print(f"epoch {epoch + 1:4d}  train_loss={float(loss):.4f}  "
                  f"val_loss={val_loss:.4f}")
    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        probs_va = torch.sigmoid(model(X_va_t)).squeeze(1).tolist()
    threshold, _ = pick_threshold(y_va, probs_va)
    metrics = eval_lists(y_va, probs_va, threshold)
    torch.save(model.state_dict(), MODEL_PATH)
    return threshold, metrics


def main() -> int:
    ap = argparse.ArgumentParser(description="Обучение модели «хорошая цена или нет»")
    ap.add_argument("--csv", default=str(DEFAULT_CSV))
    ap.add_argument("--model", choices=["histgb", "mlp"], default="histgb",
                    help="histgb — градиентный бустинг (рекомендуется), mlp — PyTorch")
    ap.add_argument("--labels", choices=["quantile", "median"], default="quantile",
                    help="quantile — нижние 35%% группы, median — цена <= медианы")
    ap.add_argument("--quantile", type=float, default=0.35)
    ap.add_argument("--epochs", type=int, default=600)
    ap.add_argument("--patience", type=int, default=60)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    if args.model == "mlp":
        try:
            import torch  # noqa: F401
        except ImportError:
            print("Нужен torch: pip install torch")
            return 1
    return train(args)


if __name__ == "__main__":
    sys.exit(main())
