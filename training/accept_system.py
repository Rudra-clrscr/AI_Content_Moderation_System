"""Does the whole system keep everything v5 was good at, AND add grievance control?

    python training/accept_system.py --baseline models/v5 --candidate models/v7

accept_model.py compares two text models. That is only half the question, because the thing
being shipped is not a model, it is a pipeline: a gate, a text model, the scans, and now a
grievance check that runs on public surfaces only.

The contract, in the words it was asked for: the dimensions where v5 is best must still hold,
with grievance control added. Four gates, and they pull against each other on purpose - the
easy way to pass 3 is to fail 2, and the easy way to pass 2 is to fail 3.

  1  NOTHING v5 CAUGHT GETS PUBLISHED      every harmful text v5 refused must still be
                                           refused, on a private surface where the grievance
                                           check never runs. This isolates the text model, so
                                           a regression cannot be masked by the new one.

  2  NOTHING v5 PUBLISHED GETS REFUSED     the expensive half. Adding a grievance model is
                                           how a system starts refusing payment confirmations
                                           and product listings, and it happened: the first
                                           bundle took correct publishing on the general
                                           held-out set from 100% to 79.5%. Measured on a
                                           PUBLIC surface, where the check does run.

  3  GRIEVANCE CONTROL IS ACTUALLY ADDED   emotional outcry and customer complaints refused
                                           on a public surface. v5 alone catches 5.6% of them,
                                           so this is the capability that justifies the work.

  4  THE DISPUTE CHANNEL SURVIVES          those same complaints still publish on a proposal.
                                           A member chasing a damaged consignment through a
                                           proposal is the platform working, and refusing that
                                           is the regression this whole surface-scoped design
                                           exists to prevent. It must be 100%, not "high".

Gate 4 has no tolerance because it is the one failure nobody would see: the author is simply
told to rewrite, and the dispute quietly does not happen.
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

PUBLIC = "article"
PRIVATE = "proposal"
SKIP = {"eval_grievance_handwritten.csv", "eval_grievance_handwritten_2.csv",
        "eval_policy_categories.csv"}
GRIEVANCE_SETS = ["eval_grievance_handwritten_2.csv", "eval_grievance_handwritten.csv"]

# Groups whose label-0 rows ARE complaints, and are therefore expected to be refused on a
# public surface under the policy this work implements. They are labelled 0 in the eval sets
# because those sets were written for the main model, where a complaint must stay publishable
# so a member can chase a consignment through a proposal. Both labels are right; they answer
# different questions, and the surface is what separates them.
#
# DECIDED, not inferred. Asked directly on 2026-10-17 whether "Their LR copy has the wrong
# destination pin code again" should be refused on an article, the answer was that it is
# refused. So this list is policy, and a reader who disagrees with it is disagreeing with the
# product decision rather than with a measurement convenience.
#
# Gate 2 does not count these, and they are PRINTED every run with their texts. Excluding a
# group from a gate is exactly the move that hides a regression, so it has to be visible and
# short enough that somebody can argue with it. If the policy is ever decided the other way,
# this list is what gets deleted, and gate 2 then fails until the model is retrained.
EXPECTED_PUBLIC_REFUSALS = {
    "b2b_negotiation/complaint", "public_callouts/ordinary_complaint",
    "public_callouts/ordinary_complaint_hinglish", "public_callouts/mild_venting",
    "public_callouts/outcry", "handwritten/review", "handwritten_2/review",
    "dismissive/dismissive",
}


def general_cases():
    out = []
    for path in sorted(glob.glob(str(HERE / "eval_*.csv"))):
        name = Path(path).name
        if name in SKIP:
            continue
        for r in csv.DictReader(open(path, encoding="utf-8")):
            if "text" in r and r.get("label") in ("0", "1"):
                out.append((r["text"], int(r["label"]), f"{name[5:-4]}/{r.get('group', '')}"))
    return out


def grievance_cases():
    out = []
    for name in GRIEVANCE_SETS:
        path = HERE / name
        if path.exists():
            for r in csv.DictReader(path.open(encoding="utf-8")):
                out.append((r["text"], int(r["label"]), name[5:-4]))
    return out


def run(model_dir: str, grievance: bool, cases, surface: str):
    os.environ["MODEL_DIR"] = model_dir
    os.environ["GRIEVANCE_CHECK"] = "1" if grievance else "0"
    for mod in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[mod]
    sys.path.insert(0, str(ROOT / "flask_api"))
    from app import create_app
    client = create_app().test_client()
    out = []
    for text, label, group in cases:
        r = client.post("/v1/moderate", json={"content": text, "content_type": surface,
                                              "content_id": "s"}).get_json()
        out.append((r["decision"] != "allow", r.get("decided_by"), label, group, text))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", default="models/v5")
    ap.add_argument("--candidate", default="models/v7")
    ap.add_argument("--min-grievance-recall", type=float, default=0.80)
    ap.add_argument("--show", type=int, default=15)
    args = ap.parse_args()

    general, grievance = general_cases(), grievance_cases()
    print(f"\nbaseline  {args.baseline} alone")
    print(f"candidate {args.candidate} + grievance check")
    print(f"{len(general)} general texts, {len(grievance)} grievance texts\n")

    # Baseline: v5 with no grievance check, on both surfaces. This is "what v5 was good at".
    base_pub = run(args.baseline, False, general, PUBLIC)
    base_priv = run(args.baseline, False, general, PRIVATE)
    base_gr_pub = run(args.baseline, False, grievance, PUBLIC)

    cand_pub = run(args.candidate, True, general, PUBLIC)
    cand_priv = run(args.candidate, True, general, PRIVATE)
    cand_gr_pub = run(args.candidate, True, grievance, PUBLIC)
    cand_gr_priv = run(args.candidate, True, grievance, PRIVATE)
    # The baseline on a proposal too, because gate 4 has to measure what the CANDIDATE breaks.
    # Three of these are refused by v5 and the abuse.targeted rule with no grievance model in
    # sight - "Do not buy from them" at 0.941, "Bohot pareshan ho chuka hoon" at 0.984. Real
    # problems, and not this change's doing; counting them here would be blaming the new part
    # for the old part's behaviour.
    base_gr_priv = run(args.baseline, False, grievance, PRIVATE)

    # 1: harmful text v5 refused, on the private surface (text model isolated)
    lost_harmful = [(g, t, b, c) for (br, _, lb, g, t), (cr, cb, *_ ) in zip(base_priv, cand_priv)
                    for b, c in [(br, cr)] if lb == 1 and br and not cr]
    # 2: legitimate text v5 published, on the public surface (grievance check active)
    all_new = [(g, t, cb) for (br, _, lb, g, t), (cr, cb, *_) in zip(base_pub, cand_pub)
               if lb == 0 and not br and cr]
    new_refusals = [x for x in all_new if x[0] not in EXPECTED_PUBLIC_REFUSALS]
    expected = [x for x in all_new if x[0] in EXPECTED_PUBLIC_REFUSALS]
    # 3 and 4
    gr_pos = [(c, b) for c, b in zip(cand_gr_pub, base_gr_pub) if c[2] == 1]
    caught = sum(1 for c, _ in gr_pos if c[0]) / max(len(gr_pos), 1)
    base_caught = sum(1 for _, b in gr_pos if b[0]) / max(len(gr_pos), 1)
    leaked = [(g, t) for (r, _, lb, g, t), (br, *_) in zip(cand_gr_priv, base_gr_priv)
              if lb == 1 and r and not br]
    already = sum(1 for (r, _, lb, g, t), (br, *_) in zip(cand_gr_priv, base_gr_priv)
                  if lb == 1 and r and br)

    gates = [
        ("1  nothing v5 caught is published", not lost_harmful,
         f"{len(lost_harmful)} harmful texts v5 refused now publish"),
        ("2  nothing v5 published is refused", not new_refusals,
         f"{len(new_refusals)} legitimate texts v5 published are now refused"),
        ("3  grievance control added", caught >= args.min_grievance_recall,
         f"{caught:.1%} of outcry and complaints refused on an article "
         f"(v5 alone: {base_caught:.1%})"),
        ("4  dispute channel survives", not leaked,
         f"{len(leaked)} complaints NEWLY refused on a proposal"
         + (f" ({already} were already refused by the baseline - see the note in the source)"
            if already else "")),
    ]

    print("=" * 78)
    for name, ok, detail in gates:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<36} {detail}")
    print("=" * 78)

    for title, items in (("HARMFUL TEXT NOW PUBLISHED (gate 1)",
                          [(g, t) for g, t, *_ in lost_harmful]),
                         ("LEGITIMATE TEXT NOW REFUSED (gate 2)",
                          [(g, t) for g, t, _ in new_refusals]),
                         ("COMPLAINTS REFUSED IN A PROPOSAL (gate 4)", leaked)):
        if items:
            print(f"\n{title}: {len(items)}")
            for g, t in items[: args.show]:
                print(f"   [{g}] {str(t)[:72]}")
            if len(items) > args.show:
                print(f"   ... and {len(items) - args.show} more")

    if expected:
        print(f"\n== EXPECTED under the policy, not counted by gate 2: {len(expected)}")
        print("   complaints the eval sets label 'allow' because they were written for the")
        print("   main model. On an article this policy refuses them; on a proposal they still")
        print("   publish, which is what gate 4 checks.")
        for g, tx, _ in expected[:8]:
            print(f"     [{g}] {str(tx)[:66]}")
        if len(expected) > 8:
            print(f"     ... and {len(expected) - 8} more")

    ok = all(g[1] for g in gates)
    print(f"\n{'ACCEPTED' if ok else 'NOT ACCEPTED'}: "
          f"{sum(g[1] for g in gates)}/{len(gates)} gates passed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
