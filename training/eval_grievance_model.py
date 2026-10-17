"""Measure a grievance bundle or checkpoint on the sets that were not generated.

    python training/eval_grievance_model.py --ckpt D:/AI/bonc-grievance/ckpt
    python training/eval_grievance_model.py --bundle ../flask_api/models/grievance-v1

The generated test split is not reported here, on purpose. It said AUC 1.0000 for the linear
model and the training loss for the fine-tune reached 0.0000 by step 1800 of 10528 - a task
the generator made separable is separable, and that number measures the generator.

What is reported:

  * the two hand-written sets, sentence by sentence when they are wrong
  * the threshold sweep, so the recall being bought with each point of false positives is
    visible rather than asserted
  * the sentences that prompted this whole line of work, which must refuse on an article
  * and the half that matters more - the SAME sentences on a proposal, which must not be
    asked at all. A model that scores 1.0 on every one of them is still correct as long as
    the pipeline never consults it there, so that is checked through the pipeline, not the
    model.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "flask_api"))

SETS = ("eval_grievance_handwritten.csv", "eval_grievance_handwritten_2.csv")

# The two submissions that started this. Both were published; both must now be refused on a
# public surface, and both must still go through in a proposal.
THE_ONES_THAT_GOT_THROUGH = [
    "that company's delivery system is very bad",
    "The driver left my fragile package out in the pouring rain instead of placing it under "
    "the covered porch",
]


def load(path: Path):
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    return ([r["text"] for r in rows], np.array([int(r["label"]) for r in rows]),
            [r.get("note", "") for r in rows])


def sweep(y, p) -> None:
    print("    threshold   caught   wrongly refused")
    for t in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        pred = p >= t
        rec = (pred & (y == 1)).sum() / max((y == 1).sum(), 1)
        fpr = (pred & (y == 0)).sum() / max((y == 0).sum(), 1)
        print(f"      {t:.2f}      {rec:6.1%}   {fpr:6.1%}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ckpt", default=None, help="a torch checkpoint (GPU)")
    ap.add_argument("--bundle", default=str(ROOT / "flask_api" / "models" / "grievance-v1"))
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args()

    if args.ckpt:
        from torch_scorer import TorchScorer
        scorer = TorchScorer(args.ckpt, args.bundle, device=args.device)
        score = lambda t: float(scorer.score(t).risk_score)                      # noqa: E731
        name = f"checkpoint {args.ckpt}"
    else:
        from app.grievance import GrievanceCheck
        check = GrievanceCheck.load(args.bundle)
        if not check.enabled:
            return 2
        score = check.probability
        name = f"bundle {args.bundle} ({check.version})"

    threshold = args.threshold
    if threshold is None:
        try:
            import json
            threshold = json.loads(
                (Path(args.bundle) / "grievance_meta.json").read_text(encoding="utf-8"))["threshold"]
        except Exception:
            threshold = 0.5
    print(f"{name}\nthreshold {threshold:.2f}\n")

    for filename in SETS:
        path = HERE / filename
        if not path.exists():
            continue
        texts, y, notes = load(path)
        p = np.array([score(t) for t in texts])
        pred = p >= threshold
        rec = (pred & (y == 1)).sum() / max((y == 1).sum(), 1)
        fpr = (pred & (y == 0)).sum() / max((y == 0).sum(), 1)
        print(f"  {filename}  n={len(texts)}")
        print(f"    caught {rec:.1%}   wrongly refused {fpr:.1%}")
        sweep(y, p)
        wrong = [(t, s, yy, n) for t, s, yy, n in zip(texts, p, y, notes)
                 if (s >= threshold) != (yy == 1)]
        for t, s, yy, n in wrong:
            print(f"    {'MISSED  ' if yy == 1 else 'REFUSED '} {s:.3f}  {t[:70]}  [{n}]")
        print()

    print("  the two that got through:")
    for t in THE_ONES_THAT_GOT_THROUGH:
        s = score(t)
        print(f"    {s:.3f}  {'REFUSED' if s >= threshold else 'STILL PUBLISHES'}  {t[:66]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
