"""Choose the triage threshold by what it does to decisions, not to spans.

    python tune_triage.py

`train_triage.py` picks a threshold so that the filter never clears a span the model would
have flagged. That is stricter than it needs to be. A cleared span only matters if the post's
**decision** changes, and it usually can't: the whole text is still scored by the full model
(v4 gives a scam-carrying article 0.999 on its own), the gate still runs, and the targeted
check still runs. The sentence scan is a safety net, so skipping part of it is only a
regression when that net was the thing holding the decision up.

So: run every held-out text through the real pipeline with the filter off, then again at each
candidate threshold, and report decision changes, model calls and latency. The highest
threshold that changes no decision is the operating point, backed off by a safety factor.
Writes the result into the bundle's triage_meta.json.
"""
from __future__ import annotations

import argparse
import gc
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
API = HERE.parent / "flask_api"
sys.path.insert(0, str(API))
logging.disable(logging.WARNING)

from app import create_app                      # noqa: E402
from app.config import load_settings            # noqa: E402
from app.routing import Thresholds              # noqa: E402
from app.sinks import MemorySink                # noqa: E402
from app.triage import TriageFilter             # noqa: E402
from torch_scorer import make_scorer            # noqa: E402


class CountingScorer:
    """Wraps the real scorer and counts how many texts reach the model."""

    def __init__(self, inner):
        self.inner = inner
        self.version = inner.version
        self.batch_invariant = getattr(inner, "batch_invariant", False)
        self.texts = 0

    def score(self, text):
        self.texts += 1
        return self.inner.score(text)

    def score_batch(self, texts, max_batch: int = 32):
        self.texts += len(texts)
        return self.inner.score_batch(texts, max_batch)


def load_texts(test_n: int) -> pd.DataFrame:
    frames = []
    for name in ("eval_handwritten", "eval_handwritten_2", "eval_articles"):
        d = pd.read_csv(HERE / f"{name}.csv", keep_default_na=False)
        frames.append(d[["text", "label"]].assign(set=name))
    test = Path("D:/AI/bonc-data/test.csv")
    if test.exists() and test_n:
        t = pd.read_csv(test, keep_default_na=False)
        t = t[t["kind"].isin(["whole", "mixed"]) & (t["text"].str.count(r"[.!?\n]") >= 2)]
        frames.append(t.sample(min(test_n, len(t)), random_state=4)[["text", "label"]].assign(set="test_split"))
    return pd.concat(frames, ignore_index=True)


DEVICE = "cpu"      # set from --device; the deployed service is always ONNX on CPU


def run(threshold: float | None, texts: pd.DataFrame, model_dir: Path) -> tuple[pd.DataFrame, int, float]:
    """One pass over every text, recording each text's own model calls and latency.
    threshold None = filter off."""
    s = load_settings(API / "config" / "settings.yaml")
    s.model_dir = model_dir
    s.label_weights = None
    s.max_chars = 50_000
    s.result_sink = "log"
    s.triage = TriageFilter() if threshold is None else TriageFilter.load(API / "models" / "triage-v1", threshold)
    if threshold is not None and not s.triage.enabled:
        raise SystemExit("triage bundle did not load; run train_triage.py first")
    meta = json.loads((model_dir / "model_meta.json").read_text(encoding="utf-8"))
    if meta.get("recommended_thresholds"):
        s.thresholds = Thresholds(**meta["recommended_thresholds"])
    # Each pass builds its own ONNX session (371 MB). Without this the machine starts paging
    # a few passes in and the latency numbers become nonsense (measured: 183 -> 3923 ms/text).
    gc.collect()
    scorer = CountingScorer(make_scorer(DEVICE, model_dir, **({} if DEVICE == "cuda" else s.model_kwargs())))
    client = create_app(s, scorer=scorer, sink=MemorySink()).test_client()
    rows = []
    for text in texts["text"]:
        before, t0 = scorer.texts, time.perf_counter()
        b = client.post("/v1/moderate", json={"content": text, "content_type": "article"}).get_json()
        rows.append((b["decision"], b["decided_by"], b["risk_score"],
                     scorer.texts - before, (time.perf_counter() - t0) * 1000))
    out = pd.DataFrame(rows, columns=["decision", "by", "risk", "calls", "ms"])
    return out, scorer.texts, float(out["ms"].mean())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model-dir", default=str(API / "models" / "v4"))
    ap.add_argument("--bundle", default=str(API / "models" / "triage-v1"))
    ap.add_argument("--test-n", type=int, default=150)
    ap.add_argument("--thresholds", type=float, nargs="+",
                    default=[0.0005, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.4])
    ap.add_argument("--safety-factor", type=float, default=0.5)
    ap.add_argument("--write", action="store_true", help="write the chosen threshold into triage_meta.json")
    ap.add_argument("--device", choices=["cpu", "cuda"], default="cpu",
                    help="cuda sweeps on the GPU (~12x faster on batched spans) via the checkpoint "
                         "behind the same pipeline; ms/text is then a GPU number, so confirm the "
                         "chosen threshold with --device cpu before shipping it")
    args = ap.parse_args()
    global DEVICE
    DEVICE = args.device

    model_dir = Path(args.model_dir)
    texts = load_texts(args.test_n)
    print(f"{len(texts)} texts: " + ", ".join(f"{k} {v}" for k, v in texts['set'].value_counts().items()))

    base, base_calls, base_ms = run(None, texts, model_dir)
    correct = ((base["decision"] == "allow") == (texts["label"] == 0)).mean()
    print(f"\nfilter off ({args.device}): {base_calls} model calls, {base_ms:.0f} ms per text, "
          f"{correct:.1%} decisions correct\n")
    print(f"{'threshold':>10} {'calls':>7} {'saved':>7} {'ms/text':>8} {'changed':>8} {'PUBLISHED':>8}")
    print(f"{'':10} {'':7} {'':7} {'':8} {'any':>8} {'now-ok':>8}")

    results, passes = [], {}
    for t in args.thresholds:
        got, calls, ms = run(t, texts, model_dir)
        passes[t] = got
        changed = got["decision"].to_numpy() != base["decision"].to_numpy()
        # The only direction that matters: something that was held back is now published.
        # reject <-> revise is not a regression — neither is published, and the author is told
        # what to fix either way — so requiring *no* change at all rejected safe thresholds.
        worse = changed & (base["decision"].to_numpy() != "allow") & (got["decision"].to_numpy() == "allow")
        results.append((t, calls, ms, int(changed.sum()), int(worse.sum())))
        print(f"{t:10.5f} {calls:7d} {1 - calls / base_calls:6.1%} {ms:8.0f} {int(changed.sum()):8d} "
              f"{int(worse.sum()):8d}")
        for i in np.flatnonzero(changed)[:4]:
            print(f"           {base['decision'][i]}/{base['by'][i]} -> {got['decision'][i]}/{got['by'][i]}"
                  f"  {texts['text'][i][:90]!r}")

    # Production traffic is overwhelmingly legitimate, and the savings are very different on
    # the two paths: allowed posts pay for the sentence scan (which the filter shortcuts),
    # rejected ones pay mostly for the word-by-word scan (which it doesn't touch).
    allowed = base["decision"].to_numpy() == "allow"
    top = max(t for t, *_ in results)
    got_best = passes[top]
    print(f"\n== where the saving lands (at threshold {top})")
    for name, mask in (("allowed posts", allowed), ("rejected posts", ~allowed)):
        b_calls, g_calls = base["calls"][mask].sum(), got_best["calls"][mask].sum()
        b_ms, g_ms = base["ms"][mask].mean(), got_best["ms"][mask].mean()
        print(f"   {name:15s} n={int(mask.sum()):4d}  calls {b_calls:5d} -> {g_calls:5d} "
              f"({1 - g_calls / max(1, b_calls):5.1%})   {b_ms:6.0f} -> {g_ms:6.0f} ms/text")
    long_articles = allowed & (texts["text"].str.len() > 600).to_numpy()
    if long_articles.any():
        b_ms, g_ms = base["ms"][long_articles].mean(), got_best["ms"][long_articles].mean()
        b_c, g_c = base["calls"][long_articles].sum(), got_best["calls"][long_articles].sum()
        print(f"   {'long + allowed':15s} n={int(long_articles.sum()):4d}  calls {b_c:5d} -> {g_c:5d} "
              f"({1 - g_c / max(1, b_c):5.1%})   {b_ms:6.0f} -> {g_ms:6.0f} ms/text")

    safe = [r for r in results if r[4] == 0]        # nothing held back becomes published
    if not safe:
        print("\nevery threshold lets something through that was caught; keep the trained (conservative) one")
        return
    best = max(safe, key=lambda r: 1 - r[1] / base_calls)
    chosen = round(best[0] * args.safety_factor, 6)
    saved = 1 - best[1] / base_calls
    print(f"\nhighest threshold that publishes nothing new: {best[0]} ({saved:.1%} fewer model calls, "
          f"{best[2]:.0f} ms/text vs {base_ms:.0f}; {best[3]} decisions moved between reject and revise)")
    print(f"shipping {chosen} (safety factor {args.safety_factor})")

    if args.write:
        path = Path(args.bundle) / "triage_meta.json"
        meta = json.loads(path.read_text(encoding="utf-8"))
        allowed_saving = 1 - got_best["calls"][allowed].sum() / max(1, base["calls"][allowed].sum())
        meta["threshold"] = chosen
        meta["threshold_chosen_by"] = ("tune_triage.py: highest threshold at which nothing that was held "
                                       "back becomes published, on the held-out sets, times the safety factor")
        meta["model_calls_saved_on_allowed_posts"] = round(float(allowed_saving), 4)
        meta["decision_tuning"] = {
            "device": args.device, "texts": int(len(texts)), "baseline_model_calls": int(base_calls),
            "baseline_ms_per_text": round(base_ms, 1),
            "allowed_posts": {"n": int(allowed.sum()),
                              "calls": [int(base["calls"][allowed].sum()), int(got_best["calls"][allowed].sum())],
                              "ms_per_text": [round(float(base["ms"][allowed].mean()), 1),
                                              round(float(got_best["ms"][allowed].mean()), 1)]},
            "sweep": [{"threshold": t, "model_calls": c, "calls_saved": round(1 - c / base_calls, 4),
                       "ms_per_text": round(m, 1), "decisions_changed": ch, "caught_then_allowed": w}
                      for t, c, m, ch, w in results],
        }
        path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
