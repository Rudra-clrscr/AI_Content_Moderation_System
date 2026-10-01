"""100,000 Sentence Comprehensive System Evaluation Suite.

Evaluates the live moderation system (Layer 1 Gate + Layer 2 DeBERTa-v3 ONNX Model v5 + Routing)
against a randomized, held-out sample of 100,000 sentences from out-of-sample test and validation sets.

Measures:
  - Accuracy, Precision, Recall, Specificity, F1, Balanced Accuracy
  - Type I (False Rejections / Alpha risk) & Type II (Missed Violations / Beta risk)
  - Motorola Six Sigma Quality metrics (Defects, DPMO, Yield, Sigma Level, Wilson 95% CI)
  - Granular breakdown across all categories (pricing idioms, Hinglish, B2B trade, scams, phishing, hate, etc.)
  - Granular breakdown across sources (synthetic, dataset123 real web data, fragments, etc.)
  - Granular breakdown across kinds (whole, sentence, mixed, title)
  - Inference speed, throughput, and sample defect inspection
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import NormalDist

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from app.config import load_settings
from app.gate import Gate
from app.model import OnnxScorer
from app.routing import Thresholds

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("eval_100k")


# --------------------------------------------------------------------------- Statistics Helpers

def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score confidence interval."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    margin = (z / denom) * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return max(0.0, center - margin), min(1.0, center + margin)


def sigma_level(defect_rate: float) -> float:
    """Motorola Six Sigma convention: long-term yield -> z, plus 1.5-sigma shift."""
    if defect_rate <= 1e-12:
        return 6.0
    if defect_rate >= 1.0 - 1e-12:
        return 0.0
    p = min(max(1.0 - defect_rate, 1e-12), 1.0 - 1e-12)
    return NormalDist().inv_cdf(p) + 1.5


# --------------------------------------------------------------------------- Main Evaluation

def load_data(n_samples: int = 100_000, seed: int = 42) -> pd.DataFrame:
    """Load and sample 100,000 sentences from held-out test and validation splits."""
    test_path = Path("D:/AI/bonc-data/test.csv")
    val_path = Path("D:/AI/bonc-data/val.csv")

    log.info("Loading held-out evaluation pools: %s and %s", test_path, val_path)
    df_test = pd.read_csv(test_path)
    df_val = pd.read_csv(val_path)

    combined = pd.concat([df_test, df_val], ignore_index=True)
    log.info("Combined pool total rows: %d (Test: %d, Val: %d)", len(combined), len(df_test), len(df_val))

    # Deduplicate by text to ensure every sample is unique
    combined = combined.drop_duplicates(subset=["text"]).reset_index(drop=True)
    log.info("Unique held-out pool size: %d rows", len(combined))

    if len(combined) < n_samples:
        log.warning("Unique pool has %d rows, requested %d. Using all %d rows.", len(combined), n_samples, len(combined))
        sample_df = combined.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    else:
        sample_df = combined.sample(n=n_samples, random_state=seed).reset_index(drop=True)

    log.info("Sampled exactly %d sentences for evaluation.", len(sample_df))
    log.info("Ground truth label breakdown:\n%s", sample_df["label"].value_counts().to_dict())
    return sample_df


def run_evaluation(df: pd.DataFrame, batch_size: int = 64, chunk_size: int = 5_000) -> dict:
    settings = load_settings()
    model_dir = settings.model_dir
    log.info("Initializing Gate and Model from %s ...", model_dir)

    gate = Gate.from_yaml(settings.gate_patterns_file)
    scorer = OnnxScorer(model_dir, intra_op_threads=6, inter_op_threads=1)
    thresholds = Thresholds(settings.thresholds.allow_max, settings.thresholds.reject_min)
    log.info("System Thresholds: allow_max=%.2f, reject_min=%.2f", thresholds.allow_max, thresholds.reject_min)

    n_total = len(df)
    results = []
    t_start = time.perf_counter()

    for chunk_idx, start_pos in enumerate(range(0, n_total, chunk_size)):
        chunk_t0 = time.perf_counter()
        end_pos = min(start_pos + chunk_size, n_total)
        chunk_df = df.iloc[start_pos:end_pos]
        texts = chunk_df["text"].astype(str).tolist()

        # Step 1: Layer 1 Gate Check
        gate_results = [gate.check(t) for t in texts]

        # Step 2: Layer 2 Model Scoring in batch
        model_scores = scorer.score_batch(texts, max_batch=batch_size)

        # Step 3: Decision Logic
        for i, (row, g_res, m_res) in enumerate(zip(chunk_df.to_dict(orient="records"), gate_results, model_scores)):
            text = row["text"]
            label = int(row["label"])
            category = str(row.get("category", "unknown"))
            kind = str(row.get("kind", "unknown"))
            source = str(row.get("source", "unknown"))

            risk = m_res.risk_score
            model_decision = "reject" if risk >= thresholds.reject_min else "allow"

            # Combined system decision
            if g_res.blocked:
                final_decision = "reject"
                decided_by = "gate_block"
            elif g_res.needs_revision:
                final_decision = "revise"
                decided_by = "gate_revise"
            else:
                final_decision = model_decision
                decided_by = "model"

            # Determine correctness
            # Safe (0) -> allow is correct; revise or reject is a defect (False Positive / False Rejection)
            # Unsafe (1) -> revise or reject is correct; allow is a defect (False Negative / Missed Violation)
            if label == 0:
                is_correct = (final_decision == "allow")
                defect_type = None if is_correct else "false_rejection"
            else:
                is_correct = (final_decision in ("revise", "reject"))
                defect_type = None if is_correct else "false_allow"

            results.append({
                "label": label,
                "category": category,
                "kind": kind,
                "source": source,
                "risk": risk,
                "model_decision": model_decision,
                "final_decision": final_decision,
                "decided_by": decided_by,
                "is_correct": is_correct,
                "defect_type": defect_type,
                "text": text[:160]  # snippet for inspection
            })

        chunk_dur = time.perf_counter() - chunk_t0
        processed = end_pos
        running_correct = sum(1 for r in results if r["is_correct"])
        running_acc = (running_correct / processed) * 100.0
        running_defects = processed - running_correct
        running_dpmo = (running_defects / processed) * 1_000_000

        progress_msg = (
            f"[{chunk_idx+1:02d}/{(n_total + chunk_size - 1)//chunk_size:02d}] "
            f"Processed {processed:,}/{n_total:,} texts ({processed/n_total*100:.1f}%) | "
            f"Speed: {len(chunk_df)/chunk_dur:.1f} sent/s | "
            f"Acc: {running_acc:.2f}% | DPMO: {running_dpmo:,.0f} | "
            f"Defects: {running_defects:,}"
        )
        print(progress_msg, flush=True)
        try:
            prog_path = ROOT.parent / "flask_api" / "scripts" / "benchmark_progress.txt"
            with open(prog_path, "w", encoding="utf-8") as pf:
                pf.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n{progress_msg}\n")
        except Exception:
            pass

    total_time = time.perf_counter() - t_start
    throughput = n_total / total_time
    log.info("Evaluation completed in %.2f s (%.1f sent/sec)", total_time, throughput)

    # ----------------------------------------------------------------------- Metrics Aggregation
    tp = sum(1 for r in results if r["label"] == 1 and not r["is_correct"] is False and r["final_decision"] in ("revise", "reject"))
    tn = sum(1 for r in results if r["label"] == 0 and r["final_decision"] == "allow")
    fp = sum(1 for r in results if r["label"] == 0 and r["final_decision"] in ("revise", "reject"))
    fn = sum(1 for r in results if r["label"] == 1 and r["final_decision"] == "allow")

    n_safe = tn + fp
    n_unsafe = tp + fn
    total = tp + tn + fp + fn
    accuracy = (tp + tn) / total
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    specificity = tn / (tn + fp) if (tn + fp) > 0 else 0.0
    balanced_acc = (recall + specificity) / 2.0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0

    frr = fp / n_safe if n_safe > 0 else 0.0
    fnr = fn / n_unsafe if n_unsafe > 0 else 0.0

    total_defects = fp + fn
    defect_rate = total_defects / total
    dpmo = defect_rate * 1_000_000
    sigma = sigma_level(defect_rate)
    w_low, w_high = wilson_interval(total_defects, total)

    # Breakdown by Category
    cat_stats = {}
    for cat in sorted(set(r["category"] for r in results)):
        cat_rows = [r for r in results if r["category"] == cat]
        c_n = len(cat_rows)
        c_correct = sum(1 for r in cat_rows if r["is_correct"])
        c_defects = c_n - c_correct
        c_dpmo = (c_defects / c_n) * 1_000_000
        c_sigma = sigma_level(c_defects / c_n)
        cat_stats[cat] = {
            "n": c_n,
            "correct": c_correct,
            "defects": c_defects,
            "accuracy": c_correct / c_n,
            "dpmo": c_dpmo,
            "sigma": c_sigma,
            "fp": sum(1 for r in cat_rows if r["defect_type"] == "false_rejection"),
            "fn": sum(1 for r in cat_rows if r["defect_type"] == "false_allow"),
        }

    # Breakdown by Source
    source_stats = {}
    for src in sorted(set(r["source"] for r in results)):
        src_rows = [r for r in results if r["source"] == src]
        s_n = len(src_rows)
        s_correct = sum(1 for r in src_rows if r["is_correct"])
        s_defects = s_n - s_correct
        source_stats[src] = {
            "n": s_n,
            "correct": s_correct,
            "defects": s_defects,
            "accuracy": s_correct / s_n,
            "dpmo": (s_defects / s_n) * 1_000_000,
            "sigma": sigma_level(s_defects / s_n),
        }

    # Breakdown by Kind
    kind_stats = {}
    for knd in sorted(set(r["kind"] for r in results)):
        knd_rows = [r for r in results if r["kind"] == knd]
        k_n = len(knd_rows)
        k_correct = sum(1 for r in knd_rows if r["is_correct"])
        k_defects = k_n - k_correct
        kind_stats[knd] = {
            "n": k_n,
            "correct": k_correct,
            "defects": k_defects,
            "accuracy": k_correct / k_n,
            "dpmo": (k_defects / k_n) * 1_000_000,
            "sigma": sigma_level(k_defects / k_n),
        }

    # Decision distribution
    decisions = Counter(r["final_decision"] for r in results)
    decided_by = Counter(r["decided_by"] for r in results)

    # Sample defects for qualitative inspection
    sample_fps = [r for r in results if r["defect_type"] == "false_rejection"][:15]
    sample_fns = [r for r in results if r["defect_type"] == "false_allow"][:15]

    summary = {
        "dataset": {
            "total_sentences": total,
            "safe_count": n_safe,
            "unsafe_count": n_unsafe,
            "pool_source": "Held-out test.csv & val.csv (Round 6 790k dataset)",
        },
        "performance": {
            "total_time_seconds": round(total_time, 2),
            "throughput_sent_sec": round(throughput, 1),
            "batch_size": batch_size,
        },
        "classification_metrics": {
            "accuracy": accuracy,
            "precision": precision,
            "recall": recall,
            "specificity": specificity,
            "balanced_accuracy": balanced_acc,
            "f1": f1,
            "confusion_matrix": {
                "true_positives": tp,
                "true_negatives": tn,
                "false_positives": fp,
                "false_negatives": fn,
            },
            "rates": {
                "false_rejection_rate_alpha": frr,
                "missed_violation_rate_beta": fnr,
            }
        },
        "six_sigma_quality": {
            "opportunities": total,
            "defects": total_defects,
            "defect_rate": defect_rate,
            "dpmo": dpmo,
            "yield_percent": (1.0 - defect_rate) * 100.0,
            "sigma_level": sigma,
            "wilson_95_ci_defect_rate": [w_low, w_high],
        },
        "decisions": dict(decisions),
        "decided_by": dict(decided_by),
        "categories": cat_stats,
        "sources": source_stats,
        "kinds": kind_stats,
        "sample_false_rejections": sample_fps,
        "sample_false_allows": sample_fns,
    }

    return summary


def main():
    parser = argparse.ArgumentParser(description="Evaluate 100k random sentences")
    parser.add_argument("--n", type=int, default=100_000, help="Number of sentences to evaluate")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for sampling")
    parser.add_argument("--batch-size", type=int, default=64, help="Batch size for ONNX inference")
    parser.add_argument("--chunk-size", type=int, default=5_000, help="Chunk size for live reporting")
    parser.add_argument("--out-json", default="flask_api/scripts/benchmark_100k_report.json")
    args = parser.parse_args()

    sample_df = load_data(n_samples=args.n, seed=args.seed)
    summary = run_evaluation(sample_df, batch_size=args.batch_size, chunk_size=args.chunk_size)

    out_path = Path(args.out_json)
    if not out_path.is_absolute():
        if str(args.out_json).startswith("flask_api"):
            out_path = ROOT.parent / args.out_json
        else:
            out_path = ROOT / args.out_json
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    log.info("Full metrics report saved to %s", out_path)

    # Print high-level summary to console
    print("\n" + "=" * 80)
    print(f"100,000 SENTENCE BENCHMARK RESULTS ({summary['performance']['throughput_sent_sec']} sent/sec)")
    print("=" * 80)
    cm = summary["classification_metrics"]["confusion_matrix"]
    print(f"Total Sentences: {summary['dataset']['total_sentences']:,}")
    print(f"Accuracy:         {summary['classification_metrics']['accuracy']*100:.2f}%")
    print(f"Precision:        {summary['classification_metrics']['precision']*100:.2f}%")
    print(f"Recall:           {summary['classification_metrics']['recall']*100:.2f}%")
    print(f"F1 Score:         {summary['classification_metrics']['f1']:.4f}")
    print(f"Specificity:      {summary['classification_metrics']['specificity']*100:.2f}%")
    print(f"False Rejections (FP): {cm['false_positives']:,} (FRR: {summary['classification_metrics']['rates']['false_rejection_rate_alpha']*100:.3f}%)")
    print(f"Missed Violations (FN): {cm['false_negatives']:,} (FNR: {summary['classification_metrics']['rates']['missed_violation_rate_beta']*100:.3f}%)")
    print(f"Six Sigma Level:  {summary['six_sigma_quality']['sigma_level']:.2f} Sigma")
    print(f"DPMO:             {summary['six_sigma_quality']['dpmo']:,.0f}")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
