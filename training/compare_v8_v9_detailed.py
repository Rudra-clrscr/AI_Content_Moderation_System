import csv
import glob
import json
import os
import sys
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "flask_api"))

from app.model import OnnxScorer

def evaluate_suite(scorer: OnnxScorer, csv_path: Path):
    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if "text" in r and r.get("label") in ("0", "1"):
                rows.append({
                    "text": r["text"],
                    "label": int(r["label"]),
                    "group": r.get("group", "default")
                })
    if not rows:
        return None

    y_true = np.array([r["label"] for r in rows])
    scores = np.array([scorer.score(r["text"]).risk_score for r in rows])
    preds = (scores >= 0.5).astype(int)

    acc = float((preds == y_true).mean())
    pos_mask = (y_true == 1)
    neg_mask = (y_true == 0)

    recall = float((preds[pos_mask] == 1).mean()) if pos_mask.any() else 1.0
    prec = float((y_true[preds == 1] == 1).mean()) if (preds == 1).any() else 1.0
    f1 = float(2 * prec * recall / (prec + recall)) if (prec + recall) > 0 else 0.0

    false_allows = int(((preds == 0) & (y_true == 1)).sum())
    false_rejects = int(((preds == 1) & (y_true == 0)).sum())

    # Per-group accuracy
    groups = {}
    for r, y, p, s in zip(rows, y_true, preds, scores):
        g = r["group"]
        if g not in groups:
            groups[g] = {"total": 0, "correct": 0, "pos": 0, "neg": 0, "false_allow": 0, "false_reject": 0}
        groups[g]["total"] += 1
        if y == 1:
            groups[g]["pos"] += 1
            if p == 0:
                groups[g]["false_allow"] += 1
        else:
            groups[g]["neg"] += 1
            if p == 1:
                groups[g]["false_reject"] += 1
        if p == y:
            groups[g]["correct"] += 1

    group_acc = {g: round(v["correct"] / v["total"], 4) for g, v in groups.items()}

    return {
        "n": len(rows),
        "pos": int(pos_mask.sum()),
        "neg": int(neg_mask.sum()),
        "acc": round(acc, 4),
        "recall": round(recall, 4),
        "prec": round(prec, 4),
        "f1": round(f1, 4),
        "false_allows": false_allows,
        "false_rejects": false_rejects,
        "group_acc": group_acc
    }

def main():
    sc_v8 = OnnxScorer("flask_api/models/v8")
    sc_v9 = OnnxScorer("flask_api/models/v9")

    eval_files = sorted(glob.glob(str(HERE / "eval_*.csv")))
    results = {}

    print(f"{'Evaluation Suite':<32} | {'Metric':<14} | {'Model v8':<12} | {'Model v9':<12} | {'Diff':<8}")
    print("=" * 86)

    for p in eval_files:
        name = Path(p).name
        res_v8 = evaluate_suite(sc_v8, Path(p))
        res_v9 = evaluate_suite(sc_v9, Path(p))
        if not res_v8 or not res_v9:
            continue

        results[name] = {"v8": res_v8, "v9": res_v9}
        diff_acc = res_v9["acc"] - res_v8["acc"]
        sign = "+" if diff_acc >= 0 else ""
        print(f"{name:<32} | Accuracy       | {res_v8['acc']*100:6.2f}%      | {res_v9['acc']*100:6.2f}%      | {sign}{diff_acc*100:+.2f}%")
        print(f"{'':<32} | F1-Score       | {res_v8['f1']:6.4f}       | {res_v9['f1']:6.4f}       | {res_v9['f1'] - res_v8['f1']:+6.4f}")
        print(f"{'':<32} | False Allows   | {res_v8['false_allows']:<12} | {res_v9['false_allows']:<12} | {res_v9['false_allows'] - res_v8['false_allows']:+d}")
        print(f"{'':<32} | False Rejects  | {res_v8['false_rejects']:<12} | {res_v9['false_rejects']:<12} | {res_v9['false_rejects'] - res_v8['false_rejects']:+d}")
        print("-" * 86)

    out_file = HERE / "v8_vs_v9_comparison.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nDetailed per-group comparison saved to {out_file}")

if __name__ == "__main__":
    main()
