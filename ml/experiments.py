"""OOF-бенчмарк вариантов улучшения обучения «хорошая цена или нет».

Честная оценка: стратифицированная 5-fold CV, все метрики считаются по
out-of-fold вероятностям (модель каждого фолда видит только train-часть).

Запуск: python ml/experiments.py [--csv data/features.csv]
"""
from __future__ import annotations

import argparse
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from features import CATEGORICAL_FEATURES, NUMERIC_FEATURES
from ml.train_price_model import DEFAULT_CSV, Preprocessor, area_bucket, load_rows

N_FOLDS = 5
SEED = 42

# Цена и её производные — ИСКЛЮЧЕНЫ из признаков: метка строится из цены
# (price <= медиана группы), и дерево, видя price, просто переучивает
# правило разметки (AUC 0.99 — утечка, а не качество).
PRICE_FEATURES = {"price", "price_per_m2", "price_per_room",
                  "log_price", "src_price_ratio"}


# ---------- метки ----------

def _groups(rows, use_district: bool) -> dict[tuple, list[int]]:
    groups = defaultdict(list)
    for i, r in enumerate(rows):
        district = (r.get("district") or "").strip().lower() if use_district else ""
        key = ((r.get("rooms") or "").strip(),
               district,
               area_bucket(_f(r.get("area"))))
        groups[key].append(i)
    return groups


def _f(v):
    try:
        x = float(str(v).strip().replace(" ", "").replace(",", "."))
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def labels_median(rows) -> list[int]:
    """Текущее правило: price <= медианы (rooms, district, area_bucket)."""
    groups = _groups(rows, use_district=True)
    by_price = defaultdict(list)
    for key, idxs in groups.items():
        for i in idxs:
            p = _f(rows[i].get("price"))
            if p:
                by_price[key].append(p)
    gmed = statistics.median([p for ps in by_price.values() for p in ps])
    labels = []
    for i, r in enumerate(rows):
        key = ((r.get("rooms") or "").strip(),
               (r.get("district") or "").strip().lower(),
               area_bucket(_f(r.get("area"))))
        ps = by_price[key]
        med = statistics.median(ps) if len(ps) >= 3 else gmed
        labels.append(int(_f(r.get("price")) <= med))
    return labels


def labels_quantile(rows, q: float = 0.35) -> list[int]:
    """Хорошая цена = цена (или цена/м²) в нижних q-перцентилех своей группы.

    С площадью: цена/м² в группе (rooms, area_bucket).
    Без площади: цена в группе (rooms, district) — цена без нормализации
    смещена к большим квартирам, поэтому отдельно.
    """
    by_val = defaultdict(list)   # (rooms, area_bucket) -> price_per_m2
    by_price = defaultdict(list)  # (rooms, district) -> price
    for i, r in enumerate(rows):
        p, a = _f(r.get("price")), _f(r.get("area"))
        rooms = (r.get("rooms") or "").strip()
        if p and a:
            by_val[(rooms, area_bucket(a))].append(p / a)
        elif p:
            by_price[(rooms, (r.get("district") or "").strip().lower())].append(p)

    def _thr(values, fallback_pool):
        if len(values) >= 10:
            return statistics.quantiles(values, n=10)[int(q * 10) - 1]
        pool = values or fallback_pool
        return (statistics.quantiles(pool, n=10)[int(q * 10) - 1]
                if len(pool) >= 20 else 0.0)

    all_p_m2 = [v for vs in by_val.values() for v in vs]
    all_price = [v for vs in by_price.values() for v in vs]
    g_m2 = statistics.quantiles(all_p_m2, n=10)[int(q * 10) - 1] if len(all_p_m2) >= 20 else 0.0
    g_price = statistics.quantiles(all_price, n=10)[int(q * 10) - 1] if len(all_price) >= 20 else 0.0

    labels = []
    for i, r in enumerate(rows):
        p, a = _f(r.get("price")), _f(r.get("area"))
        rooms = (r.get("rooms") or "").strip()
        if p and a:
            thr = _thr(by_val[(rooms, area_bucket(a))], [g_m2])
            labels.append(int(p / a <= thr))
        elif p:
            thr = _thr(by_price[(rooms, (r.get("district") or "").strip().lower())],
                       [g_price])
            labels.append(int(p <= thr))
        else:
            labels.append(0)
    return labels


# ---------- дедупликация ----------

def dedupe_cross_site(rows) -> list[dict]:
    """Одна квартира на нескольких сайтах -> одна строка с min-ценой."""
    groups = defaultdict(list)
    for r in rows:
        rooms, area, district = (r.get("rooms") or "").strip(), _f(r.get("area")), (r.get("district") or "").strip().lower()
        if rooms and area and district:
            groups[(rooms, round(area), district)].append(r)
    merged, consumed = [], set()
    out = []
    for key, group in groups.items():
        if len(group) < 2:
            continue
        cheapest = min(group, key=lambda r: _f(r.get("price")) or 1e12)
        clone = dict(cheapest)
        clone["sources_count"] = len(group)
        out.append(clone)
        consumed.update(id(r) for r in group)
    for r in rows:
        if id(r) not in consumed:
            out.append(r)
    return out


# ---------- признаки ----------

def enrich_rows(rows) -> tuple[list[dict], list[str]]:
    """Дополнительные числовые признаки (без утечки метки)."""
    by_src = defaultdict(list)
    for r in rows:
        p = _f(r.get("price"))
        if p:
            by_src[(r.get("source") or "?").strip().lower()].append(p)
    src_med = {s: statistics.median(ps) for s, ps in by_src.items() if ps}
    extra_cols = ["src_price_ratio", "rooms_area", "n_amenities", "log_price"]
    out = []
    for r in rows:
        r = dict(r)
        p, a = _f(r.get("price")), _f(r.get("area"))
        rooms = _f(r.get("rooms"))
        med = src_med.get((r.get("source") or "?").strip().lower())
        r["src_price_ratio"] = (p / med) if (p and med) else None
        r["rooms_area"] = (rooms * a) if (rooms and a) else None
        r["n_amenities"] = sum(1 for k, v in r.items()
                               if k.startswith("tag_") and str(v) in ("1", "1.0", "True"))
        r["log_price"] = math.log(p) if p else None
        out.append(r)
    return out, NUMERIC_FEATURES + extra_cols


# ---------- модели ----------

def fit_mlp(Xtr, ytr, Xte, seed, balanced_batches=False):
    import torch
    import torch.nn as nn

    torch.manual_seed(seed)
    Xtr_t = torch.tensor(Xtr, dtype=torch.float32)
    ytr_t = torch.tensor(ytr, dtype=torch.float32).unsqueeze(1)
    Xte_t = torch.tensor(Xte, dtype=torch.float32)
    # внутренний val для early stopping (стратифицированный)
    pos = [i for i, v in enumerate(ytr) if v == 1]
    neg = [i for i, v in enumerate(ytr) if v == 0]
    rng = random.Random(seed)
    rng.shuffle(pos), rng.shuffle(neg)
    n_val = max(1, int(len(ytr) * 0.15))
    val_idx = pos[:n_val] + neg[:n_val]
    tr_idx = pos[n_val:] + neg[n_val:]
    Xv_t = torch.tensor([Xtr[i] for i in val_idx], dtype=torch.float32)
    yv_t = torch.tensor([[ytr[i]] for i in val_idx], dtype=torch.float32)
    Xi_t = torch.tensor([Xtr[i] for i in tr_idx], dtype=torch.float32)
    yi_t = torch.tensor([[ytr[i]] for i in tr_idx], dtype=torch.float32)

    model = nn.Sequential(
        nn.Linear(len(Xtr[0]), 64), nn.ReLU(), nn.Dropout(0.2),
        nn.Linear(64, 32), nn.ReLU(), nn.Dropout(0.2),
        nn.Linear(32, 1),
    )
    pos_w = torch.tensor([(ytr.count(0)) / max(ytr.count(1), 1)], dtype=torch.float32)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_w)
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    best, best_state, patience = 1e9, None, 0
    for epoch in range(600):
        model.train()
        opt.zero_grad()
        loss = loss_fn(model(Xi_t), yi_t)
        loss.backward()
        opt.step()
        model.eval()
        with torch.no_grad():
            vl = float(loss_fn(model(Xv_t), yv_t))
        if vl < best - 1e-4:
            best, best_state, patience = vl, {k: v.clone() for k, v in model.state_dict().items()}, 0
        else:
            patience += 1
            if patience >= 60:
                break
    if best_state:
        model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        return torch.sigmoid(model(Xte_t)).squeeze(1).tolist()


def fit_mlp_ensemble(Xtr, ytr, Xte, seed, k=5):
    probs = []
    for j in range(k):
        probs.append(fit_mlp(Xtr, ytr, Xte, seed + 1000 * j))
    return [sum(col) / k for col in zip(*probs)]


def fit_histgb(Xtr, ytr, Xte, seed, balanced_batches=False):
    from sklearn.ensemble import HistGradientBoostingClassifier

    clf = HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.06, min_samples_leaf=15,
        l2_regularization=1.0, class_weight="balanced", random_state=seed)
    clf.fit(Xtr, ytr)
    return clf.predict_proba(Xte)[:, 1].tolist()


# ---------- CV ----------

def stratified_folds(labels, k, seed):
    rng = random.Random(seed)
    pos = [i for i, l in enumerate(labels) if l == 1]
    neg = [i for i, l in enumerate(labels) if l == 0]
    rng.shuffle(pos), rng.shuffle(neg)
    folds_pos = [pos[i::k] for i in range(k)]
    folds_neg = [neg[i::k] for i in range(k)]
    folds = []
    for j in range(k):
        test = folds_pos[j] + folds_neg[j]
        train = [i for jj in range(k) if jj != j for i in folds_pos[jj] + folds_neg[jj]]
        rng.shuffle(train)
        folds.append((train, test))
    return folds


def metrics_at(probs, labels, idx):
    p = [probs[i] for i in idx]
    y = [labels[i] for i in idx]
    order = sorted(range(len(p)), key=lambda i: p[i])
    ranks = [0] * len(p)
    for rank, i in enumerate(order, 1):
        ranks[i] = rank
    n_pos = sum(y)
    n_neg = len(y) - n_pos
    auc = ((sum(r for r, yy in zip(ranks, y) if yy) - n_pos * (n_pos + 1) / 2)
           / max(n_pos * n_neg, 1)) if n_pos and n_neg else 0.5
    thr, f1 = best_f1_threshold_py(y, p)
    pred = [int(v >= thr) for v in p]
    tp = sum(1 for pr, yy in zip(pred, y) if pr and yy)
    fp = sum(1 for pr, yy in zip(pred, y) if pr and not yy)
    fn = sum(1 for pr, yy in zip(pred, y) if not pr and yy)
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    return auc, f1, precision, recall, thr


def best_f1_threshold_py(y, probs):
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


def run_config(name, rows, labels, fit_fn, numeric=None, k=5):
    if numeric is None:
        numeric = NUMERIC_FEATURES
    numeric = [c for c in numeric if c not in PRICE_FEATURES]
    folds = stratified_folds(labels, k, SEED)
    oof = [0.0] * len(rows)
    for fi, (train_idx, test_idx) in enumerate(folds):
        tr_rows = [rows[i] for i in train_idx]
        pp = Preprocessor(numeric=numeric).fit(tr_rows)
        Xtr = [pp.row_vector(rows[i]) for i in train_idx]
        Xte = [pp.row_vector(rows[i]) for i in test_idx]
        ytr = [labels[i] for i in train_idx]
        probs = fit_fn(Xtr, ytr, Xte, SEED + fi)
        for i, p in zip(test_idx, probs):
            oof[i] = p
    auc, f1, precision, recall, thr = metrics_at(oof, labels, list(range(len(rows))))
    print(f"{name:<38} F1={f1:.3f}  AUC={auc:.3f}  P={precision:.3f} R={recall:.3f} thr={thr:.2f}")
    return {"name": name, "f1": f1, "auc": auc, "threshold": thr}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(DEFAULT_CSV))
    args = ap.parse_args()

    raw = load_rows(Path(args.csv))
    rows = [r for r in raw if _f(r.get("price"))]
    n_area = sum(1 for r in rows if _f(r.get("area")))
    print(f"строк с ценой: {len(rows)}, из них с площадью: {n_area}\n")

    lab_med = labels_median(rows)
    lab_q = labels_quantile(rows)
    print(f"доля «хорошая цена»: median={sum(lab_med) / len(rows):.0%}, "
          f"quantile={sum(lab_q) / len(rows):.0%}\n")

    results = []
    results.append(run_config("A baseline MLP + медианные метки", rows, lab_med, fit_mlp))
    results.append(run_config("B MLP + квантильные метки", rows, lab_q, fit_mlp))
    results.append(run_config("C HistGB + медианные метки", rows, lab_med, fit_histgb))
    results.append(run_config("D HistGB + квантильные метки", rows, lab_q, fit_histgb))

    rows_e, num_e = enrich_rows(rows)
    results.append(run_config("E D + доп. признаки", rows_e, lab_q, fit_histgb, numeric=num_e))

    rows_f = dedupe_cross_site(rows)
    lab_f = labels_quantile(rows_f)
    print(f"\nпосле кросс-сайт дедупа: {len(rows_f)} строк")
    results.append(run_config("F D + кросс-сайт дедуп", rows_f, lab_f, fit_histgb))

    results.append(run_config("G MLP-ансамбль(5) + квантильные метки", rows, lab_q, fit_mlp_ensemble))

    rows_h = dedupe_cross_site(rows_e)
    lab_h = labels_quantile(rows_h)
    results.append(run_config("H HistGB+квантиль+признаки+дедуп", rows_h, lab_h, fit_histgb, numeric=num_e))

    print("\n--- Итог (sorted by F1) ---")
    for r in sorted(results, key=lambda x: -x["f1"]):
        print(f"  {r['name']:<40} F1={r['f1']:.3f} AUC={r['auc']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
