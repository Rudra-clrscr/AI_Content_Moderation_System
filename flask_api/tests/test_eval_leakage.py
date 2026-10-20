"""The evaluation sets must not be in the training data.

training/README.md calls eval_*.csv "hand-written held-out sets, never trained on", and the
whole measurement discipline in this repo rests on that sentence being true: the GENERATED
test split is reported and ignored precisely because a generator makes its own held-out rows
separable, so the hand-written sets are the part that is supposed to be ungameable.

It stopped being true without anyone deciding to break it. 86 of 766 evaluation sentences had
become training frames, and 121 appeared verbatim in res/*.csv. Measured on the shipped
grievance checkpoint, leaked rows scored 88.4% against 72.2% on clean rows of the same files -
a +16.1% memorisation premium, with six of nine affected files scoring 100% on their leaked
half.

It happened the honest way, which is why a test is the right fix rather than a note. Round 4
of generate_support_complaints.py was written FROM accept_system.py's false positives - read
the failures, cover them, exactly right - but by pasting each failing sentence in as a frame
instead of writing frames for the family it belonged to. In a diff those are identical. Only a
check can tell them apart, so here is the check.

This guards the FRAMES, which is the fast half and catches the mistake at the moment it is
made. The authoritative scan is over the generated corpora:

    python training/audit_eval_leakage.py --datasets --measure

which is not run here because it streams ~400 MB of res/*.csv and those files are not in git.
Run it before training anything, and after changing a generator.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TRAINING = ROOT / "training"
sys.path.insert(0, str(TRAINING))

pytestmark = pytest.mark.skipif(not (TRAINING / "audit_eval_leakage.py").exists(),
                                reason="training/ not present in this checkout")


def test_no_eval_sentence_is_a_training_frame():
    from audit_eval_leakage import audit

    detail, _ = audit(TRAINING)
    offenders = {name: d["leaked"] for name, d in detail.items() if d["leaked"]}

    # WHAT THIS TEST DOES NOT COVER, and it is the larger half. Frames are only one route
    # into the training data; a generator that builds sentences in a function is invisible
    # here. The authoritative scan is over the generated corpora, and it still finds 44:
    # res/policy_rows.csv holds 38 of eval_policy_categories.csv's 62 rows (61% of that eval
    # set is its own training data) and res/v6_gaps.csv holds 6 more. Those feed models/v6,
    # so clearing them means regenerating and retraining the main model - a deliberate piece
    # of work with its own decision, not something to slip into a test.
    #
    #     python training/audit_eval_leakage.py --datasets --measure
    # EMPTY, and it should stay that way. The frame-level leak is fully fixed: 86 of 766
    # evaluation sentences were training frames and now none are. An entry here is a
    # concession, so add one only with a reason and a plan to remove it.
    KNOWN: dict[str, int] = {}

    problems = []
    for name, leaked in sorted(offenders.items()):
        allowed = KNOWN.get(name, 0)
        if len(leaked) > allowed:
            problems.append(f"\n  {name}: {len(leaked)} leaked frames, {allowed} allowed")
            for x in leaked[:6]:
                problems.append(f"      label={x['label']}  {x['text'][:70]}")
                problems.append(f"          from {', '.join(x['from'])}")
    assert not problems, (
        "Evaluation sentences are also training frames, so any number measured on them is a "
        "memorisation score:" + "".join(problems) +
        "\n\n  Fix at the SOURCE: write new frames for the family and leave the eval rows "
        "alone.\n  The eval row's provenance - written by hand, before the model, by someone "
        "not\n  looking at the training data - cannot be rebuilt once spent. A frame can be "
        "rewritten\n  in a minute. See generate_support_complaints.py's ROUND 5 block for "
        "what that looks like."
    )


def test_the_known_leaks_have_not_grown():
    """The pinned counts above are a ratchet, and this is the half that makes it one.

    Pinning only an upper bound lets a fixed leak be quietly replaced by a new one somewhere
    else in the same file. This asserts the total too, so the overall number cannot drift
    sideways while every individual file still passes.
    """
    from audit_eval_leakage import audit

    detail, _ = audit(TRAINING)
    total = sum(len(d["leaked"]) for d in detail.values())
    assert total == 0, (
        f"{total} evaluation sentences are training frames; the ratchet is set at 0. "
        f"Run training/audit_eval_leakage.py to see them. This was 86 and is now 0; do not "
        f"raise the bound to make a new leak pass."
    )
