"""Does the candidate still handle every category the baseline handled?

    python training/regression_check.py --baseline ../flask_api/models/v5 \\
                                        --candidate ../flask_api/models/v6 \\
                                        --also ../flask_api/models/v6

A per-set average can hide this entirely: a model that gains ten points on implied threats and
loses every counterfeit listing looks like an improvement. v6 did a smaller version of exactly
that - it gained 42 points on implied_threats and quietly started publishing three harmful
texts v5 caught.

So the unit here is the CATEGORY (the `group` column the eval sets already carry, ~70 of them)
and, inside it, the individual text. A regression is defined per text, not per percentage:

    the baseline decided this text correctly and the candidate does not

which is the only definition that means anything for a category of two or three sentences.
Percentages on an n of 3 move 33 points at a time and say nothing.

Exit code is 1 if any regression is found, so this can gate a deployment rather than be read
and forgotten. --allow-regressions takes a count for the case where a trade is made knowingly
and written down.
"""
from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "flask_api"))

SKIP = {"eval_grievance_handwritten.csv", "eval_grievance_handwritten_2.csv"}


def load(bundle: Path):
    from app.model import ModelRegistry
    reg = ModelRegistry()
    reg.load("onnx", bundle, intra_op_threads=4, inter_op_threads=1)
    return reg


def correct(score: float, label: int, boundary: float) -> bool:
    return (score >= boundary) == (label == 1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--also", nargs="*", default=[], help="shown for context, not gated on")
    ap.add_argument("--boundary", type=float, default=0.5)
    ap.add_argument("--allow-regressions", type=int, default=0)
    ap.add_argument("--show", type=int, default=40)
    args = ap.parse_args()

    bundles = [Path(args.baseline), Path(args.candidate)] + [Path(a) for a in args.also]
    regs = [load(b) for b in bundles]
    names = [r.version.replace("deberta-v3-small-bonc-", "") for r in regs]
    base_i, cand_i = 0, 1

    rows = []
    for path in sorted(glob.glob(str(HERE / "eval_*.csv"))):
        name = Path(path).name
        if name in SKIP:
            continue
        d = pd.read_csv(path, keep_default_na=False)
        if not {"text", "label"} <= set(d.columns):
            continue
        d["group"] = d.get("group", "all")
        for r in d.itertuples():
            scores = [reg.score(r.text).risk_score for reg in regs]
            rows.append({"set": name[5:-4], "group": r.group, "label": int(r.label),
                         "text": r.text, **{f"s{i}": s for i, s in enumerate(scores)}})
    df = pd.DataFrame(rows)
    for i in range(len(regs)):
        df[f"ok{i}"] = [correct(s, l, args.boundary)
                        for s, l in zip(df[f"s{i}"], df["label"])]

    print(f"\n{len(df):,} texts across {df['set'].nunique()} sets and "
          f"{df.groupby(['set', 'group']).ngroups} categories\n")
    header = "".join(f"{n:>12}" for n in names)
    print(f"{'CATEGORY':<46}{'n':>4}{header}")
    print("-" * (50 + 12 * len(names)))

    regressions = []
    for (s, g), part in df.groupby(["set", "group"]):
        cells = ""
        for i in range(len(regs)):
            c = int(part[f"ok{i}"].sum())
            mark = ""
            if i == cand_i:
                lost = part[part[f"ok{base_i}"] & ~part[f"ok{cand_i}"]]
                if len(lost):
                    mark = " !"
                    for t in lost.itertuples():
                        regressions.append({"set": s, "group": g, "label": t.label,
                                            "base": getattr(t, f"s{base_i}"),
                                            "cand": getattr(t, f"s{cand_i}"), "text": t.text})
            cells += f"{c:>8}/{len(part):<2}{mark}" if mark else f"{c:>9}/{len(part):<2}"
        print(f"{s + ' / ' + str(g):<46}{len(part):>4}{cells}")

    print("\n" + "=" * 78)
    for i, n in enumerate(names):
        print(f"  {n:<10} {int(df[f'ok{i}'].sum()):>5}/{len(df)}  "
              f"({df[f'ok{i}'].mean():.1%} correct overall)")

    gained = df[~df[f"ok{base_i}"] & df[f"ok{cand_i}"]]
    print(f"\n  {names[cand_i]} FIXES {len(gained)} texts the baseline got wrong")
    print(f"  {names[cand_i]} LOSES {len(regressions)} texts the baseline got right")

    if regressions:
        print("\n== REGRESSIONS: " + "-" * 60)
        print("   these are categories the baseline handled and the candidate does not\n")
        for r in regressions[: args.show]:
            kind = "now PUBLISHES harmful" if r["label"] == 1 else "now REFUSES legitimate"
            print(f"  {r['set']}/{r['group']}: {kind}")
            print(f"     {names[base_i]} {r['base']:.3f} -> {names[cand_i]} {r['cand']:.3f}   "
                  f"{r['text'][:72]}")
        if len(regressions) > args.show:
            print(f"  ... and {len(regressions) - args.show} more")

    ok = len(regressions) <= args.allow_regressions
    print("\n" + ("PASS" if ok else "FAIL") +
          f": {len(regressions)} regressions, {args.allow_regressions} allowed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
