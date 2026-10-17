"""Compare two text model bundles on everything a decision depends on, not just accuracy.

    python training/compare_text_models.py ../flask_api/models/v5 ../flask_api/models/v6

e2e_eval.py already reports allowed/caught per set through the whole pipeline, and
scripts/compare_models.py reports paired significance. Neither reports the things that decide
whether a model can be OPERATED, which is what this adds:

  CALIBRATION   ECE and the reliability bins. A risk score is only a probability if 0.7 means
                "wrong about 30% of the time". The thresholds, the moderation dial and the
                revise band all assume it is.

  SATURATION    what share of scores land in the usable middle. If every score is 0.000 or
                1.000 the admin's 0-1 rating has nothing to grip: moving it from 0.5 to 0.25
                changes no decision at all. This is why the per-feature levels feature cannot
                do what it looks like it does.

  SEPARATION    AUC per set, which is threshold-free, so a model that is merely miscalibrated
                can be told apart from one that cannot rank at all.

  ROBUSTNESS    the same sentence under small edits that must not change the answer - a
                pronoun, a contraction, a trailing space, UPPERCASE. A model that moves 0.99
                on one pronoun is not measuring what its score claims to measure.

  COST          p50/p95 latency per call and bundle size on disk.

Both bundles run on CPU through app.model, which is what the service does. Nothing here
touches the gate, the scans or the grievance model: those are separate stages and mixing them
in would hide which component moved.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "flask_api"))

SETS = ["eval_handwritten.csv", "eval_handwritten_2.csv", "eval_dismissive.csv",
        "eval_articles.csv", "eval_b2b_negotiation.csv", "eval_implied_threats.csv",
        "eval_public_callouts.csv", "eval_grievance_handwritten.csv",
        "eval_grievance_handwritten_2.csv"]

# Label 1 in these means "a complaint, if PUBLISHED" - which the main model is deliberately
# built NOT to flag, because the same sentence in a proposal is a member chasing a consignment.
# They are shown per set, as the size of the gap the grievance model exists to fill, and kept
# out of the pooled calibration and saturation numbers: counting them as misses would make a
# model look badly calibrated for doing exactly what it is supposed to do.
OUT_OF_SCOPE = {"eval_grievance_handwritten.csv", "eval_grievance_handwritten_2.csv"}

# Pairs that must score the same. Each is one edit away from its partner and the edit changes
# nothing a moderator would care about.
ROBUSTNESS = [
    ("I have called their customer support six times and nobody helps.",
     "I have called customer support six times and nobody helps.", "the pronoun 'their'"),
    ("We will escalate this to your account manager.",
     "We will escalate this to their account manager.", "your -> their"),
    ("Payment terms remain 30 days net.",
     "payment terms remain 30 days net.", "leading capital"),
    ("Stainless steel sinks, 304 grade.",
     "STAINLESS STEEL SINKS, 304 GRADE.", "UPPERCASE"),
    ("The consignment has not arrived.",
     "The consignment has not arrived. ", "trailing space"),
    ("We cannot accept this delay.", "We can not accept this delay.", "cannot -> can not"),
    ("Their service was poor.", "Its service was poor.", "their -> its"),
    ("I am furious about this.", "I'm furious about this.", "contraction"),
]


def load_scorer(bundle: Path):
    from app.model import ModelRegistry
    reg = ModelRegistry()
    reg.load("onnx", bundle, intra_op_threads=4, inter_op_threads=1)
    return reg


def scores_for(reg, texts: list[str]) -> np.ndarray:
    return np.array([reg.score(t).risk_score for t in texts], dtype=np.float64)


def ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> tuple[float, list]:
    """Expected calibration error, plus the bins, because one number hides which end is wrong:
    a model over-confident only on the harmful side is a different problem from one that is
    over-confident everywhere."""
    edges = np.linspace(0, 1, bins + 1)
    total, rows = 0.0, []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi) if hi < 1 else (p >= lo) & (p <= hi)
        if not m.any():
            continue
        conf, acc, n = p[m].mean(), y[m].mean(), int(m.sum())
        total += n / len(p) * abs(conf - acc)
        rows.append((lo, hi, n, conf, acc))
    return total, rows


def auc(y: np.ndarray, p: np.ndarray) -> float:
    if len(set(y.tolist())) < 2:
        return float("nan")
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(y, p))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--boundary", type=float, default=0.5)
    ap.add_argument("--middle", type=float, nargs=2, default=(0.05, 0.95),
                    help="the band counted as 'usable middle' for saturation")
    args = ap.parse_args()

    bundles = [Path(args.old), Path(args.new)]
    regs, names = [], []
    for b in bundles:
        reg = load_scorer(b)
        regs.append(reg)
        names.append(reg.version)
    print(f"\n{'':34}  {names[0]:>26}  {names[1]:>26}")

    all_scores = {n: [] for n in names}
    all_labels = []
    print(f"\n{'SET':<34}  {'allowed  caught   AUC':>26}  {'allowed  caught   AUC':>26}")
    for filename in SETS:
        path = HERE / filename
        if not path.exists():
            continue
        d = pd.read_csv(path, keep_default_na=False)
        if "text" not in d or "label" not in d:
            continue
        texts = d["text"].astype(str).tolist()
        y = d["label"].astype(int).to_numpy()
        cells = []
        for reg, n in zip(regs, names):
            p = scores_for(reg, texts)
            all_scores[n].append(p)
            pred = p >= args.boundary
            legit = (~pred & (y == 0)).sum() / max((y == 0).sum(), 1) if (y == 0).any() else float("nan")
            caught = (pred & (y == 1)).sum() / max((y == 1).sum(), 1) if (y == 1).any() else float("nan")
            cells.append(f"{legit:7.1%} {caught:7.1%} {auc(y, p):6.3f}")
        if filename in OUT_OF_SCOPE:
            for n in names:
                all_scores[n].pop()
            mark = "   <- not this model's job"
        else:
            all_labels.append(y)
            mark = ""
        print(f"{filename[5:-4]:<34}  {cells[0]:>26}  {cells[1]:>26}{mark}")

    y_all = np.concatenate(all_labels)
    print("\n== CALIBRATION (pooled, out-of-scope sets excluded) " + "-" * 25)
    for n in names:
        p = np.concatenate(all_scores[n])
        e, rows = ece(y_all, p)
        print(f"  {n}: ECE {e:.4f}")
        for lo, hi, cnt, conf, acc in rows:
            bar = "#" * int(round(acc * 30))
            print(f"      {lo:.1f}-{hi:.1f}  n={cnt:>5}  says {conf:5.1%}  actually {acc:5.1%}  {bar}")

    lo, hi = args.middle
    print(f"\n== SATURATION (share of scores between {lo} and {hi}) " + "-" * 24)
    for n in names:
        p = np.concatenate(all_scores[n])
        mid = ((p > lo) & (p < hi)).mean()
        print(f"  {n}: {mid:.1%} usable middle   "
              f"({(p <= lo).mean():.1%} at the floor, {(p >= hi).mean():.1%} at the ceiling)")
    print("  A moderation rating of 0-1 can only move decisions inside that middle. Outside it,")
    print("  changing the dial from strict to lenient changes nothing the author would notice.")

    print("\n== ROBUSTNESS (pairs that must score alike) " + "-" * 33)
    print(f"  {'edit':<24} {names[0][-10:]:>18} {names[1][-10:]:>18}")
    for a, b, what in ROBUSTNESS:
        cells = []
        for reg in regs:
            pa, pb = reg.score(a).risk_score, reg.score(b).risk_score
            flag = "  <-- FLIPS" if (pa >= args.boundary) != (pb >= args.boundary) else ""
            cells.append(f"{pa:.3f} / {pb:.3f}{flag}")
        print(f"  {what:<24} {cells[0]:>18} {cells[1]:>18}")

    print("\n== COST " + "-" * 68)
    probe = ["We stock LED panels in six finishes with GST invoice and a two year warranty."] * 40
    for b, reg, n in zip(bundles, regs, names):
        reg.score(probe[0])
        t = []
        for text in probe:
            t0 = time.perf_counter()
            reg.score(text)
            t.append((time.perf_counter() - t0) * 1000)
        size = sum(f.stat().st_size for f in b.glob("*") if f.is_file()) / 1e6
        print(f"  {n}: p50 {np.percentile(t, 50):5.1f} ms   p95 {np.percentile(t, 95):5.1f} ms   "
              f"bundle {size:.0f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
