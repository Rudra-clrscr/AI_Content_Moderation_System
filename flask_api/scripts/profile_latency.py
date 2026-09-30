"""Where does a request's time actually go?

    python scripts/profile_latency.py
    python scripts/profile_latency.py --repeat 5 --no-triage

bench_latency.py times one model call. This times whole requests through the real pipeline and
breaks them down by phase (latency_ms.stages), because the interesting costs are not the single
call: they are how many calls a request makes and why.

It runs in-process rather than over HTTP, so the numbers are the pipeline's own.
"""
from __future__ import annotations

import argparse
import logging
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
logging.disable(logging.WARNING)

from app import create_app                      # noqa: E402
from app.config import load_settings            # noqa: E402
from app.sinks import MemorySink                # noqa: E402
from app.targeted import SentenceScan, WordScan  # noqa: E402
from app.triage import TriageFilter             # noqa: E402

LISTING = "Industrial stainless steel valves, sizes 15mm to 300mm. Test certificates provided with every order."
SENTENCES = [
    "We started with four looms in 1998 and today we run sixty.",
    "Our plant in Rajkot has been ISO 9001 certified since 2011.",
    "Every batch is tested in our own NABL accredited laboratory.",
    "Delivery across India takes five to seven working days.",
    "Payment terms are 30% advance with the balance before dispatch.",
    "We supply to hospitals, contractors and government projects.",
    "Samples are free for serious buyers who share a GST number.",
    "The monsoon months need extra planning because roads flood.",
]
SCAM = "Earn 50000 per week from home with no investment, just pay a small registration fee."


def article(sentences: int) -> str:
    body = " ".join(SENTENCES[i % len(SENTENCES)] for i in range(sentences))
    return f"How we grew our business\n\n{body}"


def runon(words: int) -> str:
    filler = "quality cotton towels and bedsheets for hotels and hospitals "
    return (filler * (words // 9)) + SCAM + " " + (filler * (words // 9))


CASES = [
    ("short listing (allow)", LISTING),
    ("10-sentence article (allow)", article(10)),
    ("40-sentence article (allow)", article(40)),
    ("short scam (reject)", SCAM),
    ("40-sentence article + scam (reject)", article(40) + " " + SCAM),
    ("long run-on + scam (reject)", runon(120)),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--no-triage", action="store_true", help="measure without the linear pre-filter")
    ap.add_argument("--no-words", action="store_true", help="measure without the word-by-word scan")
    ap.add_argument("--no-scan", action="store_true", help="measure without the sentence scan")
    args = ap.parse_args()

    s = load_settings()
    s.max_chars = 60_000
    s.result_sink = "log"
    if args.no_triage:
        s.triage = TriageFilter()
    if args.no_words:
        s.word_scan = WordScan(enabled=False)
    if args.no_scan:
        s.sentence_scan = SentenceScan(enabled=False)
    client = create_app(s, sink=MemorySink()).test_client()
    print(f"model {s.model_dir.name} · triage {'on' if s.triage.enabled else 'off'} · "
          f"word scan {'on' if s.word_scan.enabled else 'off'} · sentence scan {'on' if s.sentence_scan.enabled else 'off'}")

    order = ["gate", "whole", "triage", "scan", "targeted", "highlight", "words"]
    print(f"\n{'case':38s} {'total':>7} {'calls':>6}  " + " ".join(f"{k:>8}" for k in order))
    for name, text in CASES:
        runs = []
        for _ in range(args.repeat + 1):        # first run warms caches
            b = client.post("/v1/moderate", json={"content": text, "content_type": "article"}).get_json()
            runs.append(b)
        b = runs[-1]
        totals = [r["latency_ms"]["total"] for r in runs[1:]]
        stages = {k: statistics.median([r["latency_ms"]["stages"].get(k, 0.0) for r in runs[1:]]) for k in order}
        stages["gate"] = statistics.median([r["latency_ms"]["gate"] for r in runs[1:]])
        scanned = len([x for x in b["sentence_scores"] if x.get("by") != "prefilter"])
        calls = 1 + scanned + len(b["word_scores"]) + len(b["targeted_segments"])
        cells = " ".join(f"{stages[k]:8.1f}" for k in order)
        print(f"{name:38s} {statistics.median(totals):7.0f} {calls:6d}  {cells}   [{b['decision']}]")

    print("\ncalls = model inferences the result implies (whole text + scanned spans + word variants "
          "+ targeted segments); spans the pre-filter cleared are not counted.")


if __name__ == "__main__":
    main()
