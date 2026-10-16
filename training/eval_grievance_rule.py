"""Measure `complaint.public_grievance` against res/public_grievances.csv.

    python training/eval_grievance_rule.py

A regex rule has two numbers worth knowing and they pull against each other: how much of the
genre it catches on a public surface, and how much ordinary commercial language it refuses.
Prints both, plus the sentences it gets wrong, because a pattern list is only improvable if you
can see what slipped past it.

Also checks the half that is easy to forget: every grievance must still be PUBLISHABLE on a
proposal or a request. A rule that blocks them everywhere has not been scoped, it has just been
written, and it would refuse the dispute traffic the platform exists to carry.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "flask_api"))

PUBLIC = ("article", "post", "advertisement")
PRIVATE = ("proposal", "request", "business_proposal")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=HERE.parent / "res" / "public_grievances.csv")
    ap.add_argument("--show", type=int, default=6, help="examples of each mistake")
    args = ap.parse_args()

    from app.config import PROJECT_ROOT
    from app.gate import Gate

    gate = Gate.from_yaml(PROJECT_ROOT / "config" / "gate_patterns.yaml")
    if not args.data.exists():
        print(f"no data at {args.data} - run training/generate_public_grievances.py first",
              file=sys.stderr)
        return 2
    rows = list(csv.DictReader(args.data.open(encoding="utf-8")))

    missed, wrongly_blocked, leaked = [], [], []
    caught = allowed = 0
    for r in rows:
        text, label = r["text"], r["label"] == "1"
        blocked_public = gate.check(text, "article").blocked
        if label:
            caught += blocked_public
            if not blocked_public:
                missed.append(text)
            # the scoping half: it must still publish where the dispute belongs
            for s in PRIVATE:
                if gate.check(text, s).blocked:
                    leaked.append((s, text))
                    break
        else:
            allowed += not blocked_public
            if blocked_public:
                wrongly_blocked.append(text)

    n_pos = sum(1 for r in rows if r["label"] == "1")
    n_neg = len(rows) - n_pos
    print(f"{len(rows):,} rows from {args.data.name}\n")
    print(f"  grievances blocked on a public surface   {caught:,}/{n_pos:,}  {caught/n_pos:.1%}")
    print(f"  commercial language left alone           {allowed:,}/{n_neg:,}  {allowed/n_neg:.1%}")
    print(f"  grievances wrongly blocked in private    {len(leaked):,}/{n_pos:,}"
          f"  {len(leaked)/n_pos:.1%}   <- must be 0")

    for title, items in (("MISSED on a public surface", missed),
                         ("WRONGLY BLOCKED (ordinary language)", wrongly_blocked),
                         ("LEAKED into a private surface", [t for _, t in leaked])):
        if items:
            uniq = sorted(set(items))
            print(f"\n  {title}: {len(items):,} rows, {len(uniq)} distinct")
            for t in uniq[: args.show]:
                print(f"    {t[:94]}")
    return 0 if not leaked else 1


if __name__ == "__main__":
    raise SystemExit(main())
