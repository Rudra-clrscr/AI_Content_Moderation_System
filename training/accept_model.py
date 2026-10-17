"""The acceptance gate: is the candidate better than the baseline on EVERY angle?

    python training/accept_model.py --baseline ../flask_api/models/v5 \\
                                    --candidate ../flask_api/models/v7

One command, one verdict, because "better" was being decided per conversation and that is how
v6 shipped. v6 is better than v5 on average - 87.6% of held-out texts against 76.5% - and it
publishes OTP phishing that v5 refused. Both facts are true and only one of them was noticed
at the time.

Six gates. The candidate must pass all of them to be acceptable:

  1  NO CATEGORY REGRESSIONS   no text the baseline decided correctly may be decided wrongly,
                               across all ~70 categories. This is the one v6 would fail.
  2  NET IMPROVEMENT           it must fix more than it leaves alone, or there is no reason
                               to take the risk of changing anything.
  3  CALIBRATION               ECE no worse. The thresholds and the moderation dial both
                               assume the score is a probability.
  4  SATURATION                the usable middle (0.05 to 0.95) no smaller. A model whose
                               scores are all 0.000 or 1.000 leaves the admin's rating inert.
  5  ROBUSTNESS                no pair that differs only by a pronoun, case, a contraction or
                               a trailing space may land on opposite sides of the boundary.
  6  LATENCY                   p95 no more than 20% worse.

Gates 1 and 2 are the ones that decide whether the model is SAFER. 3 to 6 decide whether it
can be OPERATED. A model can pass one set and fail the other, which is the whole reason they
are listed separately rather than averaged into a score.
"""
from __future__ import annotations

import argparse
import glob
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "flask_api"))

SKIP = {"eval_grievance_handwritten.csv", "eval_grievance_handwritten_2.csv"}

# Categories deferred by decision, not by oversight. They STILL COUNT as failures in gate 1 -
# a deferred regression is still a regression - and are only listed separately so the number
# reads as "N real, M deferred" instead of being re-argued every run. Nothing is subtracted.
# Excluding them is the move that lets a model ship with holes nobody can see any more.
#
# Hinglish, deferred 2026-10-17 to its own training round. The two affected texts pull in
# opposite directions, so no partial fix was honest - see generate_v7_gaps.py.
DEFERRED = {"handwritten/hinglish", "dismissive/hinglish_heldout",
            "public_callouts/callout_hinglish", "public_callouts/outcry_hinglish",
            "public_callouts/ordinary_complaint_hinglish", "public_callouts/neutral_hinglish"}

ROBUSTNESS = [
    ("I have called their customer support six times and nobody helps.",
     "I have called customer support six times and nobody helps.", "pronoun 'their'"),
    ("We will escalate this to your account manager.",
     "We will escalate this to their account manager.", "your -> their"),
    ("Payment terms remain 30 days net.", "payment terms remain 30 days net.", "leading capital"),
    ("Stainless steel sinks, 304 grade.", "STAINLESS STEEL SINKS, 304 GRADE.", "UPPERCASE"),
    ("The consignment has not arrived.", "The consignment has not arrived. ", "trailing space"),
    ("We cannot accept this delay.", "We can not accept this delay.", "cannot -> can not"),
    ("Their service was poor.", "Its service was poor.", "their -> its"),
    ("I am furious about this.", "I'm furious about this.", "contraction"),
]


def load(bundle: Path):
    from app.model import ModelRegistry
    reg = ModelRegistry()
    reg.load("onnx", bundle, intra_op_threads=4, inter_op_threads=1)
    return reg


def ece(y, p, bins=10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    total = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi) if hi < 1 else (p >= lo) & (p <= hi)
        if m.any():
            total += m.sum() / len(p) * abs(p[m].mean() - y[m].mean())
    return float(total)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--boundary", type=float, default=0.5)
    ap.add_argument("--allow-regressions", type=int, default=0)
    ap.add_argument("--show", type=int, default=25)
    ap.add_argument("--informational", nargs="*", default=[],
                    help="eval sets to measure and report but NOT gate on. Use this only for "
                         "a set the model was deliberately not trained for - "
                         "eval_policy_categories.csv is the case it exists for, since the "
                         "image model's categories were scoped out of the text model. A set "
                         "moved here is still printed, so scoping something out can never "
                         "quietly become hiding it.")
    args = ap.parse_args()

    base, cand = load(Path(args.baseline)), load(Path(args.candidate))
    bn = base.version.replace("deberta-v3-small-bonc-", "")
    cn = cand.version.replace("deberta-v3-small-bonc-", "")
    print(f"\nbaseline  {bn}\ncandidate {cn}\n")

    rows = []
    for path in sorted(glob.glob(str(HERE / "eval_*.csv"))):
        name = Path(path).name
        if name in SKIP:
            continue
        d = pd.read_csv(path, keep_default_na=False)
        if not {"text", "label"} <= set(d.columns):
            continue
        if "group" not in d.columns:
            d["group"] = "all"
        for r in d.itertuples():
            rows.append({"set": name[5:-4], "group": r.group, "label": int(r.label),
                         "text": r.text, "b": base.score(r.text).risk_score,
                         "c": cand.score(r.text).risk_score,
                         "gated": name not in args.informational})
    df = pd.DataFrame(rows)
    df["ok_b"] = (df.b >= args.boundary) == (df.label == 1)
    df["ok_c"] = (df.c >= args.boundary) == (df.label == 1)

    info = df[~df.gated]
    gated = df[df.gated]
    lost = gated[gated.ok_b & ~gated.ok_c]
    fixed = gated[~gated.ok_b & gated.ok_c]
    y = df.label.to_numpy()
    ece_b, ece_c = ece(y, df.b.to_numpy()), ece(y, df.c.to_numpy())
    mid_b = float(((df.b > 0.05) & (df.b < 0.95)).mean())
    mid_c = float(((df.c > 0.05) & (df.c < 0.95)).mean())

    flips = []
    for a, b, what in ROBUSTNESS:
        ca, cb = cand.score(a).risk_score, cand.score(b).risk_score
        if (ca >= args.boundary) != (cb >= args.boundary):
            flips.append((what, ca, cb))

    probe = ["We stock LED panels in six finishes with GST invoice and a two year warranty."] * 30
    lat = {}
    for n, reg in ((bn, base), (cn, cand)):
        reg.score(probe[0])
        t = []
        for text in probe:
            t0 = time.perf_counter()
            reg.score(text)
            t.append((time.perf_counter() - t0) * 1000)
        lat[n] = float(np.percentile(t, 95))

    gates = [
        ("1  no category regressions", len(lost) <= args.allow_regressions,
         f"{len(lost)} texts the baseline got right are now wrong"),
        ("2  net improvement", len(fixed) > len(lost),
         f"fixes {len(fixed)}, loses {len(lost)}"),
        ("3  calibration no worse", ece_c <= ece_b + 1e-4,
         f"ECE {ece_b:.4f} -> {ece_c:.4f}"),
        ("4  usable middle no smaller", mid_c >= mid_b - 1e-4,
         f"{mid_b:.1%} -> {mid_c:.1%} of scores between 0.05 and 0.95"),
        ("5  robustness", not flips,
         f"{len(flips)} pairs land on opposite sides of the boundary"),
        ("6  latency within 20%", lat[cn] <= lat[bn] * 1.2,
         f"p95 {lat[bn]:.1f} ms -> {lat[cn]:.1f} ms"),
    ]

    print("=" * 78)
    for name, ok, detail in gates:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<32} {detail}")
    print("=" * 78)

    if len(lost):
        keys = [f"{s}/{g}" for s, g in zip(lost["set"], lost["group"])]
        deferred = lost[[k in DEFERRED for k in keys]]
        if len(deferred):
            print(f"\n  {len(deferred)} of these are in DEFERRED categories - still counted, "
                  f"because a deferred regression is still a regression:")
            for r in deferred.itertuples():
                print(f"     {r.set}/{r.group}: {bn} {r.b:.3f} -> {cn} {r.c:.3f}  {r.text[:54]}")
        print(f"\nREGRESSIONS ({len(lost)}), the gate that matters most:\n")
        for r in lost.head(args.show).itertuples():
            kind = "now PUBLISHES harmful" if r.label == 1 else "now REFUSES legitimate"
            print(f"  {r.set}/{r.group}: {kind}")
            print(f"     {bn} {r.b:.3f} -> {cn} {r.c:.3f}   {r.text[:70]}")
        if len(lost) > args.show:
            print(f"  ... and {len(lost) - args.show} more")
    if flips:
        print("\nROBUSTNESS FLIPS:\n")
        for what, a, b in flips:
            print(f"  {what}: {a:.3f} vs {b:.3f}")

    if len(info):
        # Printed, always. A set that was scoped out is still measured, so "we did not train
        # for that" can never quietly turn into "we did not look".
        il = info[info.ok_b & ~info.ok_c]
        print(f"\n== INFORMATIONAL (not gated): {len(info)} texts in "
              f"{', '.join(sorted(info['set'].unique()))}")
        print(f"   baseline {info.ok_b.mean():.1%} correct, candidate {info.ok_c.mean():.1%}; "
              f"{len(il)} texts the baseline got right are now wrong")
        for r in il.head(8).itertuples():
            kind = "now PUBLISHES harmful" if r.label == 1 else "now REFUSES legitimate"
            print(f"     {r.group}: {kind}  {r.b:.3f} -> {r.c:.3f}  {r.text[:56]}")

    ok = all(g[1] for g in gates)
    print(f"\n{'ACCEPTED' if ok else 'NOT ACCEPTED'}: "
          f"{sum(g[1] for g in gates)}/{len(gates)} gates passed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
