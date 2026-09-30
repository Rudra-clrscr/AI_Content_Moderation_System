"""End-to-end check of the whole moderation system (gate + model + sentence/word scans + routing).

    python e2e_eval.py                                  # the live bundle
    python e2e_eval.py --models models/v3 models/v4     # compare two, if you have v3 locally

For each model bundle (with its own recommended thresholds), runs every text through
POST /v1/moderate and reports, per evaluation set:

  * legitimate allowed %   (the complaint about v3: "it rejects and reviews but doesn't allow")
  * harmful caught %       (revise or reject)
  * what decided each non-allow (gate / model / targeted / sentence)
  * latency p50 / p95
  * the legitimate texts that were NOT allowed (to read and judge)

Sets: eval_handwritten.csv and eval_articles.csv (held out, hand-written) and a sample of
the test split (never trained on, but from the same sources as training).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
API = HERE.parent / "flask_api"
sys.path.insert(0, str(API))
logging.disable(logging.WARNING)

from app import create_app  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.routing import Thresholds  # noqa: E402
from app.sinks import MemorySink  # noqa: E402


DEVICE = "cpu"      # set from --device; the deployed service is always ONNX on CPU


def client_for(model_dir: Path):
    s = load_settings(API / "config" / "settings.yaml")
    s.model_dir = model_dir
    s.label_weights = None
    s.max_chars = 50_000
    s.result_sink = "log"
    meta = json.loads((model_dir / "model_meta.json").read_text(encoding="utf-8"))
    rt = meta.get("recommended_thresholds")
    if rt:
        s.thresholds = Thresholds(rt["allow_max"], rt["reject_min"])
    scorer = None
    if DEVICE == "cuda":
        from torch_scorer import make_scorer
        scorer = make_scorer("cuda", model_dir)
    return create_app(s, scorer=scorer, sink=MemorySink()).test_client(), meta["version"], s.thresholds


def load_sets(test_n: int) -> dict[str, pd.DataFrame]:
    sets = {"handwritten": pd.read_csv(HERE / "eval_handwritten.csv", keep_default_na=False),
            "handwritten_2": pd.read_csv(HERE / "eval_handwritten_2.csv", keep_default_na=False),
            "dismissive": pd.read_csv(HERE / "eval_dismissive.csv", keep_default_na=False),
            "articles": pd.read_csv(HERE / "eval_articles.csv", keep_default_na=False),
            # Business-domain hard negatives: "beat/crush your price", "we killed our defect
            # rate", complaints, and fraud-awareness writing that quotes abuse in order to
            # report it. All legitimate; the QA pass of 2026-09-30 found the pricing group
            # rejected at 0.58-0.97 and it is the largest remaining false-positive class.
            "b2b_negotiation": pd.read_csv(HERE / "eval_b2b_negotiation.csv", keep_default_na=False)}
    test = Path("D:/AI/bonc-data/test.csv")
    if test.exists():
        t = pd.read_csv(test, keep_default_na=False)
        t = t[t["kind"].isin(["whole", "mixed"])]
        multi = t["text"].str.count(r"[.!?\n]") >= 2           # multi-sentence: exercises the scans
        parts = [t[(t.label == 0) & multi].sample(min(test_n, int(((t.label == 0) & multi).sum())), random_state=4),
                 t[(t.label == 1)].sample(min(test_n, int((t.label == 1).sum())), random_state=4)]
        sets["test_split"] = pd.concat(parts).assign(group=lambda d: d["source"] + ":" + d["category"])
    return sets


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # v3 is kept out of the repo (see flask_api/README.md), so the default compares v4 alone.
    # Pass two paths to compare bundles: --models models/v3 models/v4
    ap.add_argument("--models", nargs="+", default=["models/v4"])
    ap.add_argument("--test-n", type=int, default=300)
    ap.add_argument("--show", type=int, default=12, help="legitimate texts not allowed to print per set")
    ap.add_argument("--device", choices=["cpu", "cuda"], default="cpu",
                    help="cuda scores on the GPU for a faster sweep (decisions match the shipped "
                         "bundle; see torch_scorer.verify_against_onnx). Latency is then a GPU "
                         "number, not what the CPU service will see")
    args = ap.parse_args()
    global DEVICE
    DEVICE = args.device
    sets = load_sets(args.test_n)
    summary = {}
    for m in args.models:
        c, version, thr = client_for(API / m)
        print(f"\n######## {version}  thresholds {thr.as_dict()}")
        for name, df in sets.items():
            rows = []
            for text, label in zip(df["text"], df["label"]):
                t0 = time.perf_counter()
                b = c.post("/v1/moderate", json={"content": text, "content_type": "article"}).get_json()
                rows.append((text, int(label), b["decision"], b["decided_by"], b["risk_score"],
                             (time.perf_counter() - t0) * 1000))
            r = pd.DataFrame(rows, columns=["text", "label", "decision", "by", "risk", "ms"])
            legit, harm = r[r.label == 0], r[r.label == 1]
            allowed = (legit.decision == "allow").mean() if len(legit) else float("nan")
            caught = (harm.decision != "allow").mean() if len(harm) else float("nan")
            summary[(version, name)] = (allowed, caught)
            print(f"\n== {name}: legit allowed {allowed:.1%} ({(legit.decision == 'allow').sum()}/{len(legit)}), "
                  f"harmful caught {caught:.1%} ({(harm.decision != 'allow').sum()}/{len(harm)}), "
                  f"latency p50 {np.percentile(r.ms, 50):.0f} ms, p95 {np.percentile(r.ms, 95):.0f} ms")
            print(f"   legit decisions: {dict(Counter(legit.decision))}; not-allowed by: {dict(Counter(legit[legit.decision != 'allow'].by))}")
            print(f"   harmful decisions: {dict(Counter(harm.decision))}; caught by: {dict(Counter(harm[harm.decision != 'allow'].by))}")
            for _, x in legit[legit.decision != "allow"].head(args.show).iterrows():
                print(f"   LEGIT NOT ALLOWED [{x.decision}/{x.by} risk {x.risk}] {x.text[:150]!r}")
            for _, x in harm[harm.decision == "allow"].head(args.show).iterrows():
                print(f"   HARMFUL ALLOWED [risk {x.risk}] {x.text[:150]!r}")
    print("\n######## summary (legit allowed / harmful caught)")
    for (v, n), (a, h) in summary.items():
        print(f"   {v:40s} {n:12s} {a:7.1%} / {h:7.1%}")


if __name__ == "__main__":
    main()
