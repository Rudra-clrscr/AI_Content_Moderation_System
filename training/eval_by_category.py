"""Per-category test results for the two-headed model, so an average cannot hide a hole.

    python training/eval_by_category.py --bundle flask_api/models/v7
    python training/eval_by_category.py --ckpt D:/AI/bonc-dual/ckpt --min-n 50

WHY PER CATEGORY. An overall AUC of 0.999 on 127,000 test rows is compatible with catching
almost no violent threats, because threats are 2% of the corpus and the other 98% carries the
average. The categories that matter most to a trade platform - violence, extortion,
defamation, abuse - are exactly the rare ones, so the headline number is the least informative
thing that could be reported about them. v6 is the worked example: 54% of implied threats
caught while its overall test accuracy read 97.7%.

WHICH HEAD IS SCORED FOR WHICH ROW. The two heads answer different questions and the corpora
label different things, so a category is reported against the head that has labels for it -
the m_unsafe / m_grievance masks from build_dualhead_data.py. A row labelled only for the
unsafe head says nothing about grievance performance and is not counted towards it.

WHERE THE CATEGORY COMES FROM. The dual-head splits carry text and labels, not categories, so
they are joined back by text from the corpora that do carry them: bonc-data-v6r2 for the main
side, and res/*.csv for the grievance side because its finer names (emotional_outcry,
workplace_profane, good_outcome) are what the questions are actually about - the grievance
split dir flattens them to the source file. A row whose text matches neither is reported as
`unmapped` rather than dropped, because a large unmapped bucket means the join is wrong and
the whole table should be distrusted.

BUCKETS are a reading aid over the 68 raw categories, grouped the way the product thinks about
them. The raw table is printed too: a bucket can look healthy because one large category in it
is healthy, which is the same averaging problem one level up.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "flask_api"))

# The product's own words, mapped onto the corpus's category names. Anything not listed falls
# into `other` and still appears in the raw table below.
BUCKETS = {
    "violence & threats": ["threat", "threat_implied", "veiled_threat", "collection_threat",
                           "violence", "extortion", "intimidation"],
    "scams & fraud": ["phishing", "scam_income", "advance_fee", "lottery", "payment_trick",
                      "registration_fee", "spam"],
    "abuse & offensive": ["abuse", "offensive", "toxic", "hate", "adult", "nudity"],
    "subjective accusation": ["defamation", "public_callout", "second_person_complaint",
                              "dismissive", "business_complaint"],
    "emotional outcry": ["emotional_outcry", "workplace_profane", "workplace_grievance",
                         "workplace_negation", "workplace_thin", "support_complaint",
                         "support_complaint_hi", "workplace_grievance_hi", "public_grievance"],
    "illegal & counterfeit": ["illegal", "counterfeit", "adulterated_goods"],
    "legitimate trade (must publish)": [
        "listing", "profile", "prose", "everyday", "lawful_counterpart", "firm_business",
        "b2b_negotiation", "b2b_disputes", "b2b_logistics_docs", "b2b_qa_specs",
        "procurement_tender", "hard_negative_pricing_idiom", "hinglish_b2b_trade",
        "grammar_business_prose", "real_fragment", "legitimate_commercial", "good_outcome",
        "aphorism", "contrastive", "long_article", "legitimate_employer",
        "profane_legitimate", "legitimate_employer_hi", "supplier_legitimate"],
}
OF_BUCKET = {c: b for b, cs in BUCKETS.items() for c in cs}


def category_map(main_dir: Path, res_dir: Path) -> dict[str, str]:
    """text -> category. res/ wins where both have it: its names are the finer ones."""
    out: dict[str, str] = {}
    for split in ("train", "val", "test"):
        p = main_dir / f"{split}.csv"
        if p.exists():
            d = pd.read_csv(p, keep_default_na=False)
            if "category" in d:
                out.update(dict(zip(d["text"].astype(str).str.strip(), d["category"])))
    for p in sorted(res_dir.glob("*.csv")):
        if p.stat().st_size > 300e6:
            continue
        try:
            with p.open(encoding="utf-8", errors="replace", newline="") as fh:
                r = csv.DictReader(fh)
                if not r.fieldnames or "text" not in r.fieldnames or "category" not in r.fieldnames:
                    continue
                for row in r:
                    if row.get("text") and row.get("category"):
                        out[row["text"].strip()] = row["category"]
        except Exception as exc:                                        # pragma: no cover
            print(f"  (skip {p.name}: {type(exc).__name__}: {exc})", file=sys.stderr)
    return out


def rows_for(table: pd.DataFrame, head: str):
    m, y = f"m_{head}", f"y_{head}"
    keep = table[m].to_numpy() == 1
    return table[keep], table[y].to_numpy()[keep]


def table_for(sub: pd.DataFrame, y: np.ndarray, p: np.ndarray, by: str, min_n: int,
              head: str) -> pd.DataFrame:
    from sklearn.metrics import roc_auc_score
    rows = []
    for key, idx in sub.groupby(by).groups.items():
        sel = sub.index.get_indexer(idx)
        yy, pp = y[sel], p[sel]
        if len(yy) < min_n:
            continue
        pred = pp >= 0.5
        pos, neg = yy == 1, yy == 0
        rec = float((pred & pos).sum() / pos.sum()) if pos.any() else float("nan")
        fpr = float((pred & neg).sum() / neg.sum()) if neg.any() else float("nan")
        auc = float(roc_auc_score(yy, pp)) if pos.any() and neg.any() else float("nan")
        rows.append({"category": key, "n": len(yy), "positives": int(pos.sum()),
                     "caught": rec, "wrongly_refused": fpr, "auc": auc})
    d = pd.DataFrame(rows)
    return d.sort_values("caught", na_position="last") if len(d) else d


def show(title: str, d: pd.DataFrame, worry_below: float) -> list[str]:
    print(f"\n{title}")
    if not len(d):
        print("  (nothing above --min-n)")
        return []
    print(f"  {'category':38} {'n':>8} {'pos':>7} {'caught':>8} {'wrongly':>8} {'auc':>8}")
    print("  " + "-" * 80)
    worries = []
    for _, r in d.iterrows():
        flag = ""
        if r["positives"] and r["caught"] == r["caught"] and r["caught"] < worry_below:
            flag = "  <-- WEAK"
            worries.append(f"{r['category']} ({r['caught']:.0%} of {int(r['positives'])})")
        caught = "     n/a" if r["caught"] != r["caught"] else f"{r['caught']:7.1%}"
        wrong = "     n/a" if r["wrongly_refused"] != r["wrongly_refused"] else f"{r['wrongly_refused']:7.1%}"
        auc = "     n/a" if r["auc"] != r["auc"] else f"{r['auc']:8.4f}"
        print(f"  {str(r['category'])[:38]:38} {r['n']:>8,} {r['positives']:>7,} "
              f"{caught} {wrong} {auc}{flag}")
    return worries


# Which head a hand-written set is ASKING about. Scoring a grievance set with the unsafe head
# reads as catastrophic failure and means nothing: the unsafe head is RIGHT to let "nobody has
# replied about my refund" through, because it is not harmful anywhere - that is the entire
# reason there are two heads. The first run of this harness reported 11.1% on
# eval_grievance_handwritten_2.csv for exactly that reason and nothing was wrong with it.
EXPECTED_HEAD = {
    "eval_grievance_handwritten.csv": "grievance",
    "eval_grievance_handwritten_2.csv": "grievance",
    "eval_grievance_workplace.csv": "grievance",
    "eval_public_callouts.csv": "grievance",
    "eval_b2b_negotiation.csv": "grievance",
    "eval_dismissive.csv": "unsafe",
    "eval_handwritten.csv": "unsafe",
    "eval_handwritten_2.csv": "unsafe",
    "eval_implied_threats.csv": "unsafe",
    "eval_policy_categories.csv": "unsafe",
    "eval_articles.csv": "unsafe",
}


def handwritten(score_fn, min_n: int) -> None:
    """score_fn takes a LIST of texts and returns an (n, 2) array of [unsafe, grievance]."""
    """The sets nobody generated, broken out by their own group column."""
    print(f"\n{'=' * 90}\nHAND-WRITTEN SETS, by group. These are the numbers that decide.")
    for path in sorted(HERE.glob("eval_*.csv")):
        rows = [r for r in csv.DictReader(path.open(encoding="utf-8")) if r.get("text")]
        if not rows:
            continue
        key = "group" if "group" in rows[0] else ("note" if "note" in rows[0] else None)
        P2 = score_fn([r["text"] for r in rows])
        y = np.array([int(r["label"]) for r in rows])
        want = EXPECTED_HEAD.get(path.name, "unsafe")
        print(f"\n  {path.name}  n={len(rows)}   (asks the {want.upper()} head)")
        pos, neg = y == 1, y == 0
        for hi, head in enumerate(("unsafe", "grievance")):
            pr = P2[:, hi] >= 0.5
            mark = "  <-- the one that counts" if head == want else ""
            print(f"      {head:10} caught {(pr & pos).sum() / max(pos.sum(), 1):6.1%}  "
                  f"wrongly refused {(pr & neg).sum() / max(neg.sum(), 1):6.1%}{mark}")
        # The decision the SYSTEM makes on a public surface: either head firing refuses.
        either = (P2[:, 0] >= 0.5) | (P2[:, 1] >= 0.5)
        print(f"      {'combined':10} caught {(either & pos).sum() / max(pos.sum(), 1):6.1%}  "
              f"wrongly refused {(either & neg).sum() / max(neg.sum(), 1):6.1%}"
              f"   (public surface: either head fires)")
        p = P2[:, 0 if want == "unsafe" else 1]
        if not key:
            continue
        groups: dict[str, list[int]] = {}
        for i, r in enumerate(rows):
            groups.setdefault(r[key], []).append(i)
        # Only break out groups with enough rows to mean anything; `note` columns are often
        # one row each, which is a label not a sample.
        big = {g: ix for g, ix in groups.items() if len(ix) >= max(min_n, 3)}
        for g, ix in sorted(big.items(), key=lambda kv: -len(kv[1])):
            yy, pp = y[ix], p[ix]
            pr = pp >= 0.5
            po, ne = yy == 1, yy == 0
            c = f"{(pr & po).sum() / po.sum():6.1%}" if po.any() else "   n/a"
            w = f"{(pr & ne).sum() / ne.sum():6.1%}" if ne.any() else "   n/a"
            print(f"      {g[:34]:34} n={len(ix):>4}  caught {c}  wrongly {w}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", type=Path, default=ROOT / "flask_api" / "models" / "v7")
    ap.add_argument("--ckpt", default=None, help="score a torch checkpoint instead")
    ap.add_argument("--data", type=Path, default=Path("D:/AI/bonc-dual-data"))
    ap.add_argument("--main-dir", type=Path, default=Path("D:/AI/bonc-data-v6r2"))
    ap.add_argument("--res-dir", type=Path, default=ROOT / "res")
    ap.add_argument("--min-n", type=int, default=25)
    ap.add_argument("--worry-below", type=float, default=0.80,
                    help="flag a category catching less than this fraction of its positives")
    ap.add_argument("--limit", type=int, default=0, help="debug: sample this many test rows")
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args()

    te = pd.read_csv(args.data / "test.csv", keep_default_na=False)
    if args.limit:
        te = te.sample(min(args.limit, len(te)), random_state=4)
    te = te.reset_index(drop=True)
    te["text"] = te["text"].astype(str).str.strip()

    print(f"test rows: {len(te):,}")
    cmap = category_map(args.main_dir, args.res_dir)
    te["category"] = te["text"].map(cmap).fillna("unmapped")
    te["bucket"] = te["category"].map(OF_BUCKET).fillna("other")
    unmapped = (te["category"] == "unmapped").mean()
    print(f"category join: {1 - unmapped:.1%} mapped, {unmapped:.1%} unmapped")
    if unmapped > 0.25:
        print("  WARNING: a quarter of the test set has no category. The join is probably "
              "wrong and this table should not be trusted.", file=sys.stderr)

    if args.ckpt:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        dev = torch.device(args.device if torch.cuda.is_available() else "cpu")
        tok = AutoTokenizer.from_pretrained(args.ckpt)
        model = AutoModelForSequenceClassification.from_pretrained(args.ckpt).to(dev).eval()

        def probs(texts):
            out = []
            for i in range(0, len(texts), 64):
                enc = tok(list(texts[i:i + 64]), truncation=True, max_length=256,
                          padding=True, return_tensors="pt").to(dev)
                with torch.no_grad():
                    out.append(torch.sigmoid(model(**enc).logits.float()).cpu().numpy())
            return np.concatenate(out)
        name = f"checkpoint {args.ckpt} (uncalibrated)"
    else:
        from app.model import OnnxScorer
        sc = OnnxScorer(str(args.bundle))
        if list(sc.meta.labels) != ["unsafe", "grievance"]:
            sys.exit(f"{args.bundle} is not a two-headed bundle: labels {sc.meta.labels}")

        def probs(texts):
            # score_batch, and ONE call per text: the first cut called sc.score(t) twice per
            # row to read the two labels, which doubled a 12,000-row pass for nothing.
            out = []
            for i in range(0, len(texts), 32):
                for r in sc.score_batch(list(texts[i:i + 32])):
                    out.append([r.label_scores["unsafe"], r.label_scores["grievance"]])
            return np.array(out)
        name = f"bundle {args.bundle} ({sc.meta.version})"

    print(f"scoring with {name} ...", flush=True)
    P = probs(te["text"].tolist())

    worries = {}
    for h, col in (("unsafe", 0), ("grievance", 1)):
        sub, y = rows_for(te, h)
        p = P[sub.index.to_numpy(), col]
        sub = sub.reset_index(drop=True)
        print(f"\n{'=' * 90}\nHEAD: {h}   ({len(sub):,} test rows carry this label)")
        w1 = show(f"-- by bucket --", table_for(sub, y, p, "bucket", args.min_n, h),
                  args.worry_below)
        w2 = show(f"-- by raw category (n >= {args.min_n}) --",
                  table_for(sub, y, p, "category", args.min_n, h), args.worry_below)
        worries[h] = (w1, w2)

    handwritten(probs, args.min_n)

    print(f"\n{'=' * 90}\nWEAK CATEGORIES (catching under {args.worry_below:.0%} of their positives)")
    any_weak = False
    for h, (wb, wc) in worries.items():
        if wc:
            any_weak = True
            print(f"\n  {h} head:")
            for w in wc:
                print(f"    - {w}")
    if not any_weak:
        print("  none")
    print("\nA category here is not automatically a defect - some of these are rare and some "
          "are\ndeliberately left to the Layer 1 gate - but each one is a claim somebody "
          "should check\nbefore this bundle serves traffic.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
