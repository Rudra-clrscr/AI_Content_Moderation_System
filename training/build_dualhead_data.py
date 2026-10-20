"""One dataset for the two-headed model: text, an unsafe label, a grievance label, and masks.

    python training/build_dualhead_data.py \\
        --main D:/AI/bonc-data-v6r2 \\
        --grievance D:/AI/bonc-grievance-data-v8 \\
        --out D:/AI/bonc-dual-data --main-frac 0.20

WHY TWO HEADS AND NOT ONE SCORE. The product decision is one model for the whole system. The
obstacle is not architecture, it is arithmetic: the same sentence needs two answers.

    "Nobody at their support desk has replied about the refund in two weeks."
        on an article   REFUSE   - the genre BONC does not host in public
        in a proposal   ALLOW    - a member chasing a consignment, which is the platform
                                   working, and refusing it is the regression that made the
                                   dispute channel unusable once

One binary output cannot emit both for one input. Two outputs can, because they are two
different questions rather than one question with two answers: P(unsafe) applies everywhere,
P(grievance) only on the public surfaces. The pipeline already knows the surface and simply
picks. It is also one inference call instead of today's two, so the merged model is faster
than the pair it replaces.

WHAT A MASK IS FOR. The two corpora do not label the same thing. The main corpus answers "is
this harmful anywhere" and says nothing about whether a text is a published grievance; the
grievance corpus is the reverse. A row therefore supervises ONE head and must not be allowed
to supervise the other, because "unlabelled" is not "negative":

    main row,      "First copy Gucci bags, wholesale"     y_unsafe=1   grievance MASKED
    grievance row, "Nobody has replied about my refund"   y_grievance=1  unsafe MASKED

Masking it rather than defaulting it to 0 is the whole correctness argument. Default it to 0
and every grievance row teaches the unsafe head that complaints are safe - which happens to be
true - while every main-corpus harmful row teaches the grievance head that scams are not
grievances, which is also true but is not what those rows were labelled for, and the two
corpora disagree about enough borderline text that the errors would not cancel.

Where a text appears in BOTH corpora it gets both labels, which are the most valuable rows in
the file: they are the texts that pin the difference between the two questions.

SPLIT PRECEDENCE, which is the subtle part. The two corpora were split independently, so the
same text can be in main/train and grievance/test. Honouring both is impossible and honouring
the training side would leak. Precedence is test > val > train: a text that is anybody's test
row is a test row here, even at the cost of dropping it from training. The count of rows moved
this way is printed, because a large number would mean the two corpora overlap more than
anyone intended.

--main-frac exists because the main corpus is 1.31M rows against the grievance corpus's 385k,
and the model warm-starts from the main model's own checkpoint (see train_dualhead.py), whose
unsafe head is converted exactly rather than relearned. So the main rows here are REHEARSAL -
enough to stop the unsafe head drifting while the grievance head is learned - not a from-
scratch training set. 1.0 keeps all of them and costs about two hours an epoch.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

SPLIT_RANK = {"train": 0, "val": 1, "test": 2}


def load(base: Path, which: str) -> pd.DataFrame:
    frames = []
    for split in ("train", "val", "test"):
        path = base / f"{split}.csv"
        if not path.exists():
            sys.exit(f"no {path}")
        d = pd.read_csv(path, keep_default_na=False)
        d["split"] = split
        frames.append(d)
    d = pd.concat(frames, ignore_index=True)
    d["text"] = d["text"].astype(str).str.strip()
    d = d[d["text"].str.len() > 0]
    d["label"] = d["label"].astype(int)
    d["which"] = which
    return d[["text", "label", "split", "which", "source"]]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--main", type=Path, default=Path("D:/AI/bonc-data-v6r2"),
                    help="the data dir the LIVE main model was trained on")
    ap.add_argument("--grievance", type=Path, default=Path("D:/AI/bonc-grievance-data-v8"))
    ap.add_argument("--out", type=Path, default=Path("D:/AI/bonc-dual-data"))
    ap.add_argument("--main-frac", type=float, default=0.20,
                    help="fraction of main-corpus TRAIN rows to keep as rehearsal. val and "
                         "test are always kept in full, because they are the gate.")
    ap.add_argument("--seed", type=int, default=4)
    args = ap.parse_args()

    m = load(args.main, "unsafe")
    g = load(args.grievance, "grievance")
    print(f"main      {len(m):>9,} rows  ({args.main})")
    print(f"grievance {len(g):>9,} rows  ({args.grievance})")

    both = pd.concat([m, g], ignore_index=True)

    # One row per text. The split is the strictest any corpus gave it, and the labels are
    # whatever each corpus supplied - masked where neither did.
    both["rank"] = both["split"].map(SPLIT_RANK)
    strict = both.groupby("text", sort=False)["rank"].max()

    def side(which: str) -> pd.Series:
        part = both[both["which"] == which]
        # A text duplicated inside one corpus with conflicting labels is dropped rather than
        # majority-voted: contradictory supervision on an identical string is the one failure
        # that cannot be measured away afterwards.
        agg = part.groupby("text", sort=False)["label"].agg(["max", "min"])
        ok = agg[agg["max"] == agg["min"]]["max"]
        dropped = len(agg) - len(ok)
        if dropped:
            print(f"  dropped {dropped:,} {which} texts carrying both labels within that corpus")
        return ok

    y_unsafe, y_griev = side("unsafe"), side("grievance")

    out = pd.DataFrame({"text": strict.index})
    out["split"] = strict.to_numpy()
    out["split"] = out["split"].map({v: k for k, v in SPLIT_RANK.items()})
    out["y_unsafe"] = out["text"].map(y_unsafe).fillna(0).astype(int)
    out["m_unsafe"] = out["text"].isin(y_unsafe.index).astype(int)
    out["y_grievance"] = out["text"].map(y_griev).fillna(0).astype(int)
    out["m_grievance"] = out["text"].isin(y_griev.index).astype(int)

    moved = 0
    for which, src in (("unsafe", m), ("grievance", g)):
        orig = src.groupby("text", sort=False)["split"].agg(lambda s: max(s, key=lambda x: SPLIT_RANK[x]))
        joined = out.set_index("text")["split"].reindex(orig.index)
        moved += int((joined.to_numpy() != orig.to_numpy()).sum())
    print(f"\n{moved:,} texts took a stricter split than their own corpus gave them "
          f"(test > val > train, so nothing leaks into training)")

    dual = out[(out.m_unsafe == 1) & (out.m_grievance == 1)]
    print(f"{len(dual):,} texts carry BOTH labels - the rows that pin the two questions apart")
    if len(dual):
        contested = dual[(dual.y_unsafe == 0) & (dual.y_grievance == 1)]
        print(f"  of which {len(contested):,} are 'safe anywhere' AND 'a grievance in public' "
              f"- exactly the case one score could not express")

    # Rehearsal thinning, applied to the TRAIN split only and only to rows that exist solely
    # to supervise the unsafe head. A row carrying both labels is never thinned: it is scarce
    # and it is the most informative kind here.
    if args.main_frac < 1.0:
        only_unsafe = (out.m_unsafe == 1) & (out.m_grievance == 0) & (out.split == "train")
        keep = out[only_unsafe].sample(frac=args.main_frac, random_state=args.seed).index
        before = len(out)
        out = out.drop(index=out[only_unsafe].index.difference(keep))
        print(f"\nrehearsal: kept {args.main_frac:.0%} of unsafe-only train rows "
              f"({before:,} -> {len(out):,})")

    args.out.mkdir(parents=True, exist_ok=True)
    print()
    for split in ("train", "val", "test"):
        part = out[out.split == split]
        part.to_csv(args.out / f"{split}.csv", index=False)
        u, gr = part[part.m_unsafe == 1], part[part.m_grievance == 1]
        print(f"  {split:5} {len(part):>9,} rows | unsafe-labelled {len(u):>8,} "
              f"({u.y_unsafe.mean() if len(u) else 0:5.1%} pos) | grievance-labelled "
              f"{len(gr):>7,} ({gr.y_grievance.mean() if len(gr) else 0:5.1%} pos)")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
