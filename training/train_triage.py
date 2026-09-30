"""Train the linear triage pre-filter that sits in front of the sentence scan.

    python train_triage.py --out ../flask_api/models/triage-v1

The scan scores every sentence (and every word window over a long sentence) with DeBERTa.
Nearly all of them come back safe, so most of that work is wasted. This trains a logistic
regression over hashed n-grams to recognise the obviously-safe ones in ~0.1 ms, so the
model is only called for the rest.

It is distillation, not a second opinion: the target is **what the v4 model does**, not
the dataset label. The filter's only job is "would the teacher have flagged this span?",
and a span the teacher allows is safe to skip whatever its document was labelled.

  1. Rebuild the exact spans the pipeline scans (app.targeted.scan_spans + word_windows)
     from the train/val/test splits, so the filter is trained on what it will really see.
  2. Score every span with the v4 checkpoint on the GPU, with the shipped temperature, and
     mark it positive when the teacher's risk clears `allow_max`. That, and only that, is
     the target: a span the teacher allows is free to skip no matter how its document was
     labelled, and pulling truly-unsafe-but-teacher-missed spans through would cost model
     calls without changing a single decision. Those are reported separately instead.
  3. Fit SGD logistic regression in minibatches (the full feature matrix is too big to hold).
  4. Choose the threshold on the validation spans: the highest one that misses no span the
     teacher would have flagged. Misses matter, skipped work doesn't, so the sweep is
     reported at several miss budgets and the zero-miss point is what ships.
  5. Report skip rate and misses on the test spans, then write the bundle.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from scipy.sparse import csr_matrix
from sklearn.linear_model import SGDClassifier
from sklearn.utils.class_weight import compute_class_weight
from transformers import AutoModelForSequenceClassification, AutoTokenizer

HERE = Path(__file__).resolve().parent
API = HERE.parent / "flask_api"
sys.path.insert(0, str(API))

from app.targeted import scan_spans, word_windows          # noqa: E402
from app.triage import FEATURE_BITS, FEATURE_VERSION, hash_features  # noqa: E402
from calibrate_export import softmax, torch_logits          # noqa: E402

N_FEATURES = 1 << FEATURE_BITS
# The pipeline's defaults (settings.yaml word_scan); spans must match what it really scans.
WINDOW_WORDS, WINDOW_STRIDE = 40, 20


def spans_for(text: str) -> list[str]:
    """Every span Pipeline._scan_units would score on its own for this text."""
    sentences = scan_spans(text)
    out = [text[s:e] for s, e in sentences] if len(sentences) >= 2 else []
    for s, e in sentences:
        out += [text[a:b] for a, b in word_windows(text, s, e, WINDOW_WORDS, WINDOW_STRIDE)]
    return out


def build_spans(df: pd.DataFrame, cap: int, seed: int) -> pd.DataFrame:
    """Runtime-shaped spans, deduplicated, with the label of the row they came from."""
    rows: dict[str, int] = {}
    for text, label in zip(df["text"], df["label"]):
        for span in spans_for(text):
            span = span.strip()
            if len(span) >= 2:
                rows[span] = max(rows.get(span, 0), int(label))
    # Short texts are never split, but the filter still sees them when a post has 2+
    # sentences; single-sentence posts skip the scan entirely, so they aren't included.
    items = list(rows.items())
    random.Random(seed).shuffle(items)
    items = items[:cap]
    return pd.DataFrame(items, columns=["text", "row_label"])


def featurize(texts) -> csr_matrix:
    indptr, indices, data = [0], [], []
    for t in texts:
        idx, val = hash_features(t)
        indices.extend(idx)
        data.extend(val)
        indptr.append(len(indices))
    return csr_matrix((np.asarray(data, dtype=np.float32), np.asarray(indices, dtype=np.int32),
                       np.asarray(indptr, dtype=np.int64)), shape=(len(indptr) - 1, N_FEATURES))


GRID = [round(float(t), 6) for t in np.geomspace(2e-4, 0.5, 28)]


def sweep(triage_p: np.ndarray, positive: np.ndarray, grid=GRID) -> list[tuple[float, float, int]]:
    """(threshold, skip rate, misses) for each candidate threshold."""
    return [(t, float((triage_p < t).mean()), int(((triage_p < t) & positive).sum())) for t in grid]


def best_threshold(triage_p: np.ndarray, positive: np.ndarray, budget: int, safety: float) -> tuple[float, float]:
    """Highest threshold within the miss budget, scaled down by a safety factor.

    "Zero misses on validation" is a sharp criterion on a finite sample, and the first run
    showed it slipping to one miss on test. Backing the threshold off by `safety` buys margin
    for a couple of points of skip rate.
    """
    ok = [(t, skip) for t, skip, miss in sweep(triage_p, positive) if miss <= budget]
    if not ok:
        return 0.0, 0.0
    t, _ = max(ok, key=lambda x: x[1])
    t *= safety
    return t, float((triage_p < t).mean())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="D:/AI/bonc-data")
    ap.add_argument("--ckpt", default="D:/AI/bonc-v4/ckpt", help="the v4 teacher (PyTorch checkpoint)")
    ap.add_argument("--bundle", default=str(API / "models" / "v4"), help="shipped bundle, for the temperature")
    ap.add_argument("--out", default=str(API / "models" / "triage-v1"))
    ap.add_argument("--version", default="triage-linear-v1")
    ap.add_argument("--max-train-spans", type=int, default=150_000)
    ap.add_argument("--max-eval-spans", type=int, default=40_000)
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--alphas", type=float, nargs="+", default=[1e-7, 1e-6, 1e-5, 1e-4])
    ap.add_argument("--safety-factor", type=float, default=0.5,
                    help="shipped threshold = safety-factor x the highest threshold within the miss budget")
    ap.add_argument("--allow-max", type=float, default=0.5, help="a span at or under this is allowed, so skipping it is free")
    ap.add_argument("--miss-budget", type=int, default=0, help="spans the teacher would flag that the filter may clear")
    ap.add_argument("--cache", default="D:/AI/bonc-v4/triage-cache", help="teacher-scored spans, reused between runs")
    ap.add_argument("--rescore", action="store_true", help="ignore the cache and score the spans again")
    ap.add_argument("--seed", type=int, default=4)
    args = ap.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    temperature = json.loads((Path(args.bundle) / "model_meta.json").read_text(encoding="utf-8"))["temperature"]
    tok = AutoTokenizer.from_pretrained(args.ckpt)
    teacher = AutoModelForSequenceClassification.from_pretrained(args.ckpt).to(device)
    print(f"teacher {args.ckpt} on {device}, temperature {temperature:.4f}", flush=True)

    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    parts = {}
    for name, cap in (("train", args.max_train_spans), ("val", args.max_eval_spans), ("test", args.max_eval_spans)):
        t0 = time.time()
        # Teacher scoring is the slow part (~2 min); cache it so the threshold and the
        # classifier can be re-tuned without paying for it again.
        cached = cache / f"spans_{name}_{cap}.csv"
        if cached.exists() and not args.rescore:
            spans = pd.read_csv(cached, keep_default_na=False)
        else:
            df = pd.read_csv(Path(args.data) / f"{name}.csv", keep_default_na=False)
            spans = build_spans(df, cap, args.seed)
            logits = torch_logits(teacher, tok, spans["text"].tolist(), device, 256)
            spans["teacher_p"] = softmax(logits / temperature)[:, 1]
            spans.to_csv(cached, index=False)
        spans["positive"] = spans["teacher_p"] > args.allow_max
        parts[name] = spans
        print(f"{name}: {len(spans)} spans in {time.time() - t0:.0f}s, "
              f"{spans['positive'].mean():.1%} flagged by the teacher "
              f"({(spans['row_label'] == 1).mean():.1%} came from an unsafe row)", flush=True)

    train = parts["train"]
    print("featurising...", flush=True)
    t0 = time.time()
    chunks = []
    for i in range(0, len(train), 20_000):
        part = train.iloc[i:i + 20_000]
        chunks.append((featurize(part["text"]), part["positive"].to_numpy().astype(np.int8)))
    print(f"featurised {len(train)} spans in {time.time() - t0:.0f}s "
          f"({sum(c.nnz for c, _ in chunks) / len(train):.0f} features per span)", flush=True)

    # 'balanced' isn't available with partial_fit, so compute it once from the full target.
    cw = dict(enumerate(compute_class_weight("balanced", classes=np.array([0, 1]),
                                             y=train["positive"].to_numpy().astype(int))))
    val_X = featurize(parts["val"]["text"])
    val_pos = parts["val"]["positive"].to_numpy()

    def fit(alpha: float) -> tuple[SGDClassifier, float, float]:
        # average=True (averaged SGD) instead of the last iterate: plain SGD swung between
        # 48% and 71% skip rate from one epoch to the next, which made model selection noise.
        clf = SGDClassifier(loss="log_loss", alpha=alpha, class_weight=cw, average=True,
                            random_state=args.seed)
        rng = random.Random(args.seed)
        for _ in range(args.epochs):
            order = list(range(len(chunks)))
            rng.shuffle(order)
            for j in order:
                X, y = chunks[j]
                clf.partial_fit(X, y, classes=np.array([0, 1]))
        p = clf.predict_proba(val_X)[:, 1]
        t, skip = best_threshold(p, val_pos, args.miss_budget, args.safety_factor)
        return clf, t, skip

    print("\n== regularisation sweep (skip rate at the chosen threshold, validation spans)")
    fitted = []
    for alpha in args.alphas:
        clf, t, skip = fit(alpha)
        fitted.append((skip, alpha, clf, t))
        print(f"   alpha {alpha:g}: threshold {t:.5f} -> skips {skip:.1%}", flush=True)
    skip_rate, alpha, clf, threshold = max(fitted, key=lambda x: x[0])
    print(f"   chosen alpha {alpha:g}")

    val_p = clf.predict_proba(val_X)[:, 1]
    print("\n== threshold sweep on validation spans")
    print("   threshold   skipped   misses (teacher-flagged spans cleared by the filter)")
    for t, skip, miss in sweep(val_p, val_pos):
        print(f"   {t:9.5f} {skip:9.1%} {miss:8d}")
    if threshold <= 0:
        raise SystemExit("no threshold meets the miss budget; retrain with more data or raise --miss-budget")
    misses = int(((val_p < threshold) & val_pos).sum())
    print(f"\nchosen threshold {threshold:.5f} (safety factor {args.safety_factor}) "
          f"-> skips {skip_rate:.1%} of spans, {misses} misses on validation")

    test_p = clf.predict_proba(featurize(parts["test"]["text"]))[:, 1]
    test_pos = parts["test"]["positive"].to_numpy()
    cleared = test_p < threshold
    # Secondary, and not the filter's fault: spans from unsafe rows that the teacher already
    # allows. The filter can't make those worse, but a rise here would mean it is learning to
    # clear exactly the text the model is weakest on, which is worth watching between rounds.
    unsafe_row = (parts["test"]["row_label"] == 1).to_numpy()
    teacher_already_missed = unsafe_row & ~test_pos
    print(f"test spans: skip {cleared.mean():.1%} ({int(cleared.sum())}/{len(cleared)}), "
          f"misses {int((cleared & test_pos).sum())} "
          f"(of the teacher's own misses, {int((cleared & teacher_already_missed).sum())}/"
          f"{int(teacher_already_missed.sum())} are cleared too)")
    if (cleared & test_pos).any():
        print("\nspans the filter cleared that the teacher would have flagged:")
        missed = parts["test"][cleared & test_pos].sort_values("teacher_p", ascending=False)
        print(missed[["teacher_p", "text"]].head(15).to_string())

    t0 = time.perf_counter()
    for t in parts["test"]["text"].head(2000):
        hash_features(t)
    per_span_ms = (time.perf_counter() - t0) / 2000 * 1000

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    weights = clf.coef_[0].astype(np.float32)
    np.savez_compressed(out / "triage.npz", weights=weights, intercept=np.float32(clf.intercept_[0]))
    meta = {
        "version": args.version,
        "feature_version": FEATURE_VERSION,
        "feature_bits": FEATURE_BITS,
        # `threshold` is what the service uses. This script sets it conservatively (no span the
        # teacher would flag may be cleared); tune_triage.py then raises it as far as the
        # end-to-end decisions allow and records how it chose, so the two must not be confused.
        "threshold": threshold,
        "training_threshold": threshold,
        "threshold_chosen_by": "train_triage.py: highest threshold clearing no span the teacher would flag",
        "alpha": alpha,
        "safety_factor": args.safety_factor,
        "teacher": Path(args.bundle).name,
        "allow_max": args.allow_max,
        "trained_on_spans": len(train),
        "spans_at_training_threshold": {
            "val": {"skip_rate": round(skip_rate, 4), "misses": misses, "n": len(val_p)},
            "test": {"skip_rate": round(float(cleared.mean()), 4),
                     "misses": int((cleared & test_pos).sum()),
                     "teacher_misses_also_cleared": int((cleared & teacher_already_missed).sum()),
                     "n": int(len(cleared))},
        },
        "featurize_ms_per_span": round(per_span_ms, 4),
        "sweep": [{"threshold": t, "skip_rate": round(s, 4), "misses": m} for t, s, m in sweep(val_p, val_pos)],
        "notes": "Logistic regression over hashed word 1-2 grams and char 3-5 grams, distilled from the "
                 "teacher's decisions on the spans the sentence scan really produces. Clears only spans the "
                 "teacher would have allowed; everything else still goes to the model.",
    }
    (out / "triage_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"\nwrote {out}: {sorted(p.name for p in out.iterdir())} "
          f"({(out / 'triage.npz').stat().st_size / 1e6:.1f} MB, {per_span_ms:.3f} ms per span to featurise)")


if __name__ == "__main__":
    main()
