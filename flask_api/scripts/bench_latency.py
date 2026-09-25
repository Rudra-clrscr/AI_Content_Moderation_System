"""Latency check against the sub-30ms inference budget.

    python scripts/bench_latency.py                 # uses MODEL_DIR / settings.yaml
    python scripts/bench_latency.py --n 500 --model-dir models/v2

Measures the gate and model separately, in-process (no HTTP/queue overhead).
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_settings  # noqa: E402
from app.gate import Gate  # noqa: E402
from app.model import OnnxScorer  # noqa: E402

SAMPLES = [
    "Premium basmati rice exporter, 20 years in business, FSSAI certified.",
    "We manufacture custom CNC-machined aluminium parts for automotive clients across Europe and Asia. " * 4,
    "Limited offer!!! Contact on whatsapp for bulk discount",
    "Hiring sales reps, commission only.",
]


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--model-dir")
    args = ap.parse_args()

    s = load_settings()
    gate = Gate.from_yaml(s.gate_patterns_file)
    scorer = OnnxScorer(args.model_dir or s.model_dir, **s.model_kwargs())

    gate_ms, model_ms = [], []
    for i in range(args.n):
        text = SAMPLES[i % len(SAMPLES)]
        t0 = time.perf_counter()
        gate.check(text)
        gate_ms.append((time.perf_counter() - t0) * 1000)
        model_ms.append(scorer.score(text).inference_ms)

    for name, xs in (("gate", gate_ms), ("model", model_ms)):
        print(f"{name:5s}  mean {statistics.mean(xs):6.2f}  p50 {pct(xs, 50):6.2f}  "
              f"p95 {pct(xs, 95):6.2f}  p99 {pct(xs, 99):6.2f}  max {max(xs):6.2f} ms")
    ok = pct(model_ms, 95) <= s.latency_budget_ms
    print(f"p95 inference {'within' if ok else 'OVER'} {s.latency_budget_ms:.0f} ms budget")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
