"""Train the public-surface grievance classifier.

    python training/train_grievance.py                 # -> flask_api/models/grievance-v1
    python training/train_grievance.py --report-only   # measure a built bundle, train nothing

WHAT THIS MODEL ANSWERS, AND WHY IT IS NOT THE MAIN MODEL.

    "Would this text be a customer-support complaint or an emotional outcry if it were
     PUBLISHED TO THE NETWORK?"

The main model (models/v6) answers a different question - is this text harmful anywhere - and
it must keep answering it, because a member chasing a damaged consignment through a proposal is
the platform working. One model cannot hold both: it never sees the surface, so teaching it
that "nobody has replied about my refund in two weeks" is a refusal would refuse it in the
dispute channel too. That is the regression that made the portal useless once already.

So this is a second, separate head, and the pipeline only consults it on the public surfaces -
article, post, advertisement. On a proposal it is never called, which is the only way the two
answers can coexist. Nothing in the main model's training data changes.

THE RECONCILIATION, STATED RATHER THAN IMPLIED.

res/public_callouts.csv holds 30,000 label-0 rows. About 10,400 of them, across 18 templates,
are themselves complaints - "Nobody has replied about {thing} in two weeks", "Your delivery of
{thing} was late again and it cost us the week". They were labelled allow on purpose, and for
the main model that is still the right label.

For THIS model they are positives, because the question is different: published as an article,
they are the genre BONC does not host. The other ~19,600 - "We are delighted with how {thing}
turned out", "Payment terms remain 30 days net" - stay negatives, and they are the most
valuable rows in the file, because they share every content word with the positives.

That is what reconciled means here: each template was given a side for the public-surface
question, once, in the open. No row is flipped in the main model's data and no text carries two
labels within one model. `--show-reconciled` prints the split.

WHY A LINEAR MODEL. It reuses app.triage.hash_features, so training and serving cannot drift,
and the bundle is 1 MB of float32 against 354 MB for a DeBERTa fine-tune - which matters,
because the LFS free tier is 1 GB and v6 already holds a third of it. It also costs ~0.1 ms, so
a public post pays nothing noticeable. If the held-out numbers below are not good enough, the
escalation is a real fine-tune and this file's data work is reused unchanged.

MEASUREMENT. The generated test split is reported, and ignored. The number that decides is
eval_grievance_handwritten.csv: 52 sentences written by hand, before any model existed, none of
them from a frame in any generator. A classifier trained on templates will score well on its
own held-out templates no matter how little it has learned.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "flask_api"))

from app.triage import FEATURE_BITS, FEATURE_VERSION, hash_features  # noqa: E402

RES = ROOT / "res"
N_FEATURES = 1 << FEATURE_BITS

# The 18 templates in public_callouts.csv whose label-0 rows are themselves complaints. Matched
# on the template text, not the row text, so the decision is per frame and visible here rather
# than per row and hidden in a regex. Everything not matched stays a negative.
RECONCILED_TO_POSITIVE = (
    "Frankly, performance on",
    "Nobody has replied about",
    "Quality on",
    "This is the second time",
    "We are disappointed with how",
    "We cannot keep absorbing",
    "We expected better on",
    "Your delivery of",
    "Your team missed the deadline",
    "is wrong for the third month",
    "It is frustrating, but we understand",
    "I am concerned about the timeline",
    "We are worried the monsoon",
    "We need urgent action on",
    "We will escalate",
    "Our legal team will write",
    "Humein",
    "abhi tak nahi aaya",
)


def split_of(group: str, val: float = 0.1, test: float = 0.1) -> str:
    """Group-aware, by skeleton. A row and its siblings off the same frames must never land on
    opposite sides: that is how v6 reported 99.66% accuracy while the hand-written sets said
    76%."""
    h = int(hashlib.md5(group.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "test" if h < test else "val" if h < test + val else "train"


def load_rows(show_reconciled: bool) -> pd.DataFrame:
    frames = []

    path = RES / "support_complaints.csv"
    if not path.exists():
        sys.exit(f"no {path} - run training/generate_support_complaints.py first")
    d = pd.read_csv(path, keep_default_na=False)
    d["source"] = "support_complaints"
    frames.append(d[["text", "label", "template", "source"]])

    callouts = RES / "public_callouts.csv"
    if callouts.exists():
        c = pd.read_csv(callouts, keep_default_na=False)
        # label 1 there (public callouts, emotional outcry) is label 1 here: both are the genre.
        # label 0 splits per the reconciliation above.
        is_complaint = c["template"].apply(lambda t: any(m in t for m in RECONCILED_TO_POSITIVE))
        c["label"] = np.where(c["label"] == 1, 1, np.where(is_complaint, 1, 0))
        c["source"] = "public_callouts"
        frames.append(c[["text", "label", "template", "source"]])
        if show_reconciled:
            moved = c[is_complaint & (c["source"] == "public_callouts")]
            print(f"\nRECONCILED to positive: {len(moved):,} rows that are label 0 for the main "
                  f"model\n(they stay label 0 there - this is a different question):")
            for tpl, n in moved["template"].value_counts().items():
                print(f"  {n:6,}  {tpl[:92]}")
            print()

    # Real text, not generated, from the corpora the main model trains on. This is the fix the
    # first hand-written run demanded: trained only on frames, the model refused "Stainless
    # steel sinks, 304 grade" at 0.800, because it had never seen a product listing. Listings
    # and BONC's own safe requests are negatives; BONC's 160 hand-labelled complaints are
    # positives, and they are the only on-domain positives here that nobody generated.
    #
    # `prose` (151,782 tweets), `toxic` and `offensive` are deliberately left out. Tweets are
    # full of complaints nobody labelled as such, so they would be negatives that are really
    # positives, and abuse is the main model's job - putting it on the negative side here
    # would teach this one that angry writing is fine.
    try:
        sys.path.insert(0, str(HERE))
        from prepare_data import load_res, relabel
        real = relabel(load_res())
        neg = real[real["category"].isin(["listing", "bonc_safe"])].copy()
        pos = real[real["category"] == "bonc_complaint"].copy()
        neg["label"], pos["label"] = 0, 1
        for part, name in ((neg, "real_negative"), (pos, "real_complaint")):
            part["source"] = name
            # One group per category, not per row: these carry no template, and a group per
            # row would turn the split below into a random one for 22,000 rows.
            part["template"] = name + ":" + part["category"].astype(str)
            frames.append(part[["text", "label", "template", "source"]])
        print(f"real corpora: {len(neg):,} negatives (listings, BONC requests), "
              f"{len(pos):,} positives (BONC complaints)")
    except Exception as exc:                                    # pragma: no cover
        print(f"  (skipping real corpora: {type(exc).__name__}: {exc})", file=sys.stderr)

    d = pd.concat(frames, ignore_index=True)
    d["text"] = d["text"].astype(str).str.strip()
    d = d[d["text"].str.len() > 0]
    # Contradictory supervision on identical strings is the one failure that cannot be measured
    # away afterwards, so drop any text that ended up on both sides rather than letting the
    # majority win silently.
    both = d.groupby("text")["label"].nunique()
    bad = set(both[both > 1].index)
    if bad:
        print(f"dropping {len(bad):,} texts that carry both labels across sources")
        d = d[~d["text"].isin(bad)]
    d = d.drop_duplicates(subset="text")
    d["split"] = d["template"].astype(str).map(split_of)
    return d


def featurise(texts) -> csr_matrix:
    indptr, indices, data = [0], [], []
    for t in texts:
        idx, val = hash_features(t)
        indices.extend(idx)
        data.extend(val)
        indptr.append(len(indices))
    return csr_matrix((np.asarray(data, dtype=np.float32),
                       np.asarray(indices, dtype=np.int32),
                       np.asarray(indptr, dtype=np.int64)),
                      shape=(len(indptr) - 1, N_FEATURES))


def pick_threshold(y, p) -> tuple[float, dict]:
    """The highest threshold that still catches 95% of the genre.

    One-sided on purpose, and in the opposite direction to the triage filter. A miss here
    publishes a grievance, which is the thing being fixed; a false positive refuses a case
    study, which the author can rewrite and which an admin can see. 95% is the recall floor,
    and among the thresholds that meet it the highest is taken, because that is the one that
    refuses the least legitimate writing.
    """
    best = (0.5, None)
    for t in np.arange(0.05, 0.96, 0.01):
        pred = p >= t
        recall = float((pred & (y == 1)).sum() / max((y == 1).sum(), 1))
        if recall >= 0.95:
            fpr = float((pred & (y == 0)).sum() / max((y == 0).sum(), 1))
            best = (round(float(t), 2), {"recall": recall, "false_positive_rate": fpr})
    return best


def report(name: str, y, p, threshold: float) -> dict:
    pred = p >= threshold
    pos, neg = y == 1, y == 0
    out = {
        "n": int(len(y)),
        "recall": float((pred & pos).sum() / max(pos.sum(), 1)),
        "false_positive_rate": float((pred & neg).sum() / max(neg.sum(), 1)),
        "accuracy": float((pred == (y == 1)).mean()),
    }
    if pos.any() and neg.any():
        out["auc"] = float(roc_auc_score(y, p))
        out["ap"] = float(average_precision_score(y, p))
    print(f"  {name:28} n={out['n']:>6,}  caught={out['recall']:6.1%}  "
          f"wrongly refused={out['false_positive_rate']:6.1%}"
          + (f"  AUC={out['auc']:.4f}" if "auc" in out else ""))
    return out


def handwritten(path: Path):
    if not path.exists():
        return None, None, None
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    return ([r["text"] for r in rows],
            np.array([int(r["label"]) for r in rows]),
            [r.get("note", "") for r in rows])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "flask_api" / "models" / "grievance-v1")
    ap.add_argument("--handwritten", type=Path, nargs="+",
                    default=[HERE / "eval_grievance_handwritten.csv",
                             HERE / "eval_grievance_handwritten_2.csv"])
    ap.add_argument("--show-reconciled", action="store_true")
    ap.add_argument("--write-splits", type=Path, default=None,
                    help="also write train/val/test CSVs here for the transformer path "
                         "(train.py --data <dir>), using this same group-aware split")
    ap.add_argument("--show-errors", type=int, default=8)
    ap.add_argument("--C", type=float, default=4.0)
    args = ap.parse_args()

    d = load_rows(args.show_reconciled)
    print(f"{len(d):,} rows, {d['label'].mean():.1%} positive, "
          f"{d['template'].nunique():,} skeletons")
    print(d.groupby(["source", "label"]).size().to_string())
    print()
    for s in ("train", "val", "test"):
        part = d[d.split == s]
        print(f"  {s:5} {len(part):>7,} rows  {part['label'].mean():5.1%} positive")

    if args.write_splits:
        # The transformer path reuses train.py and calibrate_export.py unchanged - they take
        # a directory of train/val/test CSVs and nothing else - so the escalation from this
        # linear model to a fine-tune is a different consumer of the same split, not a second
        # pipeline that could disagree with it.
        args.write_splits.mkdir(parents=True, exist_ok=True)
        for s in ("train", "val", "test"):
            part = d[d.split == s].copy()
            part["category"] = part["source"]
            part["kind"] = "whole"
            part[["text", "label", "category", "kind", "source"]].to_csv(
                args.write_splits / f"{s}.csv", index=False)
        print(f"\nsplits written to {args.write_splits} for train.py")

    tr, va, te = (d[d.split == s] for s in ("train", "val", "test"))
    Xtr, ytr = featurise(tr.text), tr.label.to_numpy()
    clf = LogisticRegression(C=args.C, max_iter=2000, class_weight="balanced", n_jobs=-1)
    clf.fit(Xtr, ytr)

    Xva, yva = featurise(va.text), va.label.to_numpy()
    pva = clf.predict_proba(Xva)[:, 1]
    threshold, at = pick_threshold(yva, pva)
    print(f"\nthreshold {threshold:.2f} chosen on validation "
          f"(recall {at['recall']:.1%}, false positives {at['false_positive_rate']:.1%})\n")

    metrics = {"validation": report("validation (generated)", yva, pva, threshold)}
    Xte, yte = featurise(te.text), te.label.to_numpy()
    pte = clf.predict_proba(Xte)[:, 1]
    metrics["test"] = report("test (generated, unseen frames)", yte, pte, threshold)

    # Set 1 has already informed one round of data design: its false positives are why the
    # editorial frames and the real listings above exist. That makes its next reading partly
    # a training signal rather than a measurement. Set 2 was written before this run and has
    # fed nothing, so it is the clean one until it too is used to fix something.
    print("\n  -- the numbers that decide: hand-written, no frame in common with training --")
    for path in args.handwritten:
        texts, yhw, notes = handwritten(Path(path))
        if texts is None:
            continue
        phw = clf.predict_proba(featurise(texts))[:, 1]
        metrics[Path(path).stem] = report(Path(path).stem, yhw, phw, threshold)
        wrong = [(t, p, y, n) for t, p, y, n in zip(texts, phw, yhw, notes)
                 if (p >= threshold) != (y == 1)]
        if wrong:
            print(f"      {len(wrong)} of {len(texts)} wrong:")
            for t, p, y, n in wrong[: args.show_errors]:
                kind = "MISSED  " if y == 1 else "REFUSED "
                print(f"      {kind} {p:.3f}  {t[:72]}   [{n}]")

    args.out.mkdir(parents=True, exist_ok=True)
    weights = np.zeros(N_FEATURES, dtype=np.float32)
    weights[clf.coef_[0] != 0] = clf.coef_[0][clf.coef_[0] != 0].astype(np.float32)
    np.savez_compressed(args.out / "grievance.npz", weights=weights,
                        intercept=np.float32(clf.intercept_[0]))
    meta = {
        "version": "grievance-linear-v1",
        "feature_version": FEATURE_VERSION,
        "feature_bits": FEATURE_BITS,
        "threshold": threshold,
        "surfaces": ["article", "post", "advertisement"],
        "trained_on": {"rows": int(len(d)), "skeletons": int(d["template"].nunique())},
        "metrics": metrics,
        "note": "Public surfaces only. The main model is surface-blind and must stay that way; "
                "this one answers 'is this a complaint published where it does not belong'.",
    }
    (args.out / "grievance_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    size = (args.out / "grievance.npz").stat().st_size / 1e6
    print(f"\nwrote {args.out} ({size:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
