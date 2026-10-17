"""Build the v7 train/val/test splits from v6's, plus the fixes v6 measured badly on.

    python training/build_v7_data.py --v6 D:/AI/bonc-data-v6 --out D:/AI/bonc-data-v7b

Not a re-run of prepare_data.py. v6's splits are taken as they are and added to, for one
reason: a rebuild from source would move rows between train and test, and then no comparison
between v6 and v7 would mean anything. Same splits, same boundaries, new rows only.

WHAT IS ADDED

1. res/v7_gaps.csv - the three families v6 lost or never had, with their lawful counterparts.
   See generate_v7_gaps.py for why each one is there.

2. PRONOUN DE-CORRELATION, which is the part that needs the existing rows rather than new
   ones. Measured in v6's training set:

       rows containing "their "   67,044   68.5% label 1
       base rate                           38.8% label 1

   A 1.77x lift, so "their" alone is worth a lot of logit, and v6 duly scores "I have called
   their customer support six times and nobody helps" at 0.994 and the same sentence without
   it at 0.001. Seventy-five hand-written minimal pairs cannot shift that; the correlation has
   to be removed where it was created.

   So a sample of existing rows is COPIED WITH THE PRONOUN SWAPPED, keeping the label. Taking
   mostly label-0 rows and swapping them INTO "their", and label-1 rows containing "their" and
   swapping them OUT, moves the conditional towards the base rate from both ends. The copy is
   a new row in the same group as its original, so it lands in the same split and no sibling
   leaks across the boundary.

   What this does NOT do is fix sentences where the pronoun genuinely matters - "post their
   address here" is not the same offence as "post your address here". Those live in
   generate_v7_gaps.py's PRONOUN_UNSAFE, labelled 1 under every pronoun, so the model still
   has the offence and only loses the shortcut.

3. Nothing is removed. v6's gains - implied_threats 17.5% -> 59.2%, public_callouts 5.1% ->
   59.0% - come from rows that are all still here.
"""
from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Ordered longest-first so "the firm's" is matched before "the".
PRONOUN_FORMS = ["their", "your", "its", "his", "her"]
SWAP_TARGETS = ["their", "your", "its"]


def swap_pronoun(text: str, to: str, rng) -> str | None:
    """Replace the possessive pronouns in `text` with `to`, preserving capitalisation.
    Returns None when there is nothing to swap or the swap changes nothing."""
    def repl(m: re.Match) -> str:
        word = m.group(0)
        return to[0].upper() + to[1:] if word[0].isupper() else to

    pattern = re.compile(r"\b(" + "|".join(PRONOUN_FORMS) + r")\b", re.IGNORECASE)
    out = pattern.sub(repl, text)
    return None if out == text else out


def split_of(group: str) -> str:
    """The same hash prepare_data.py uses, so a new row lands where its group already is."""
    h = int(hashlib.md5(str(group).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "test" if h < 0.1 else "val" if h < 0.2 else "train"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--v6", type=Path, default=Path("D:/AI/bonc-data-v6"))
    ap.add_argument("--out", type=Path, default=Path("D:/AI/bonc-data-v7b"))
    ap.add_argument("--gaps", type=Path, default=ROOT / "res" / "v7_gaps.csv")
    ap.add_argument("--swap-per-side", type=int, default=60_000,
                    help="pronoun-swapped copies to add on each side of the label")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    import numpy as np
    rng = np.random.default_rng(args.seed)

    splits = {}
    for name in ("train", "val", "test"):
        splits[name] = pd.read_csv(args.v6 / f"{name}.csv", keep_default_na=False)
        print(f"v6 {name}: {len(splits[name]):,} rows, {splits[name].label.mean():.3f} positive")

    train = splits["train"]
    has_their = train.text.str.contains(r"\btheir\b", case=False, regex=True)
    print(f"\nbefore: rows with 'their' {has_their.sum():,}, "
          f"{train[has_their].label.mean():.3f} positive vs {train.label.mean():.3f} base")

    # --- 2. pronoun de-correlation -----------------------------------------------------
    added = []
    # (a) label-0 rows WITHOUT "their" -> swapped INTO "their". Raises the share of safe
    #     rows that contain it, which is the direction that matters most.
    pool_a = train[(train.label == 0) & ~has_their]
    pool_a = pool_a[pool_a.text.str.contains(r"\b(?:your|its|his|her)\b", case=False, regex=True)]
    take = pool_a.sample(min(args.swap_per_side, len(pool_a)), random_state=args.seed)
    for r in take.itertuples():
        out = swap_pronoun(r.text, "their", rng)
        if out:
            added.append({"text": out, "label": 0, "category": "pronoun_swap",
                          "kind": "whole", "source": "pronoun_swap", "group": r.group
                          if hasattr(r, "group") else f"swap:{r.Index}"})
    # (b) label-1 rows WITH "their" -> swapped OUT to your/its. Lowers the share of unsafe
    #     rows that contain it, from the other end.
    pool_b = train[(train.label == 1) & has_their]
    take = pool_b.sample(min(args.swap_per_side, len(pool_b)), random_state=args.seed + 1)
    for i, r in enumerate(take.itertuples()):
        out = swap_pronoun(r.text, SWAP_TARGETS[i % 2 + 1], rng)   # your, its, your, its...
        if out:
            added.append({"text": out, "label": 1, "category": "pronoun_swap",
                          "kind": "whole", "source": "pronoun_swap", "group": r.group
                          if hasattr(r, "group") else f"swap:{r.Index}"})
    swaps = pd.DataFrame(added)
    print(f"pronoun swaps: {len(swaps):,} "
          f"({(swaps.label == 0).sum():,} into 'their', {(swaps.label == 1).sum():,} out of it)")

    # --- 1. the gap rows ---------------------------------------------------------------
    gaps = pd.read_csv(args.gaps, keep_default_na=False)
    gaps["source"] = "v7_gaps"
    gaps["split"] = gaps["template"].map(split_of)
    print(f"gap rows: {len(gaps):,} "
          f"({(gaps.split == 'train').sum():,} train / {(gaps.split == 'val').sum():,} val / "
          f"{(gaps.split == 'test').sum():,} test)")

    cols = [c for c in splits["train"].columns]
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("train", "val", "test"):
        part = [splits[name]]
        g = gaps[gaps.split == name].drop(columns=["split"])
        for c in cols:
            if c not in g.columns:
                g[c] = ""
        part.append(g[cols])
        if name == "train" and len(swaps):
            s = swaps.copy()
            for c in cols:
                if c not in s.columns:
                    s[c] = ""
            part.append(s[cols])
        merged = pd.concat(part, ignore_index=True)
        # Contradictory supervision on identical strings is the one failure that cannot be
        # measured away afterwards: a swap can collide with a real row of the other label.
        both = merged.groupby("text")["label"].nunique()
        bad = set(both[both > 1].index)
        if bad:
            print(f"  {name}: dropping {len(bad):,} texts that now carry both labels")
            merged = merged[~merged.text.isin(bad)]
        merged = merged.drop_duplicates(subset="text")
        merged.to_csv(out_dir / f"{name}.csv", index=False)
        print(f"v7 {name}: {len(merged):,} rows, {merged.label.mean():.3f} positive")

    after = pd.read_csv(out_dir / "train.csv", keep_default_na=False)
    m = after.text.str.contains(r"\btheir\b", case=False, regex=True)
    print(f"\nafter:  rows with 'their' {m.sum():,}, {after[m].label.mean():.3f} positive "
          f"vs {after.label.mean():.3f} base  "
          f"(lift {after[m].label.mean() / after.label.mean():.2f}x, was 1.77x)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
