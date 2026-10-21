"""The same wording must not mean REFUSE in one rule and ALLOW in another.

This is reachable in ordinary use and it was silent. An admin writes an allowing rule
"a photo of rainbow", then uploads a rainbow photograph, describes it the same way and marks
it refuse. The identical sentence is now in a refusing rule and an allowing rule at once, both
score the same picture, the softmax splits between them, and which one wins depends on the
other labels in the file rather than on anything anyone chose. The admin sees a rainbow
refused with nothing in the policy to explain it.

The system cannot resolve this - refusing and allowing the same thing are both coherent
policies - so it has to detect it and ask. These tests pin the detection and what resolving
does, without needing the 254 MB text encoder, because they exercise the rule arithmetic
rather than the rebuild.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.admin_policy import _norm_phrase, _resolve_conflicts, find_conflicts  # noqa: E402


def policy(**labels) -> dict:
    return {"version": 1, "labels": labels}


def test_same_wording_opposite_verdicts_is_a_conflict():
    doc = policy(rainbow={"unsafe": False, "prompts": ["a photo of rainbow"]})
    found = find_conflicts(doc, "unrest", ["a photo of rainbow"], unsafe=True)
    assert len(found) == 1
    assert found[0]["other_label"] == "rainbow"
    assert found[0]["other_verdict"] == "allow"
    assert found[0]["this_verdict"] == "refuse"


def test_case_and_punctuation_do_not_hide_a_conflict():
    """"A photo of Rainbow." and "a photo of rainbow" are the same sentence to CLIP, so a
    comparison that misses that would let the contradiction straight through."""
    doc = policy(rainbow={"unsafe": False, "prompts": ["a photo of rainbow"]})
    assert find_conflicts(doc, "unrest", ["A photo of Rainbow."], unsafe=True)
    assert _norm_phrase("A photo of  Rainbow. ") == _norm_phrase("a photo of rainbow")


def test_same_verdict_is_not_a_conflict():
    """Two refusing rules sharing a wording is redundant, not contradictory. Flagging it would
    train people to click past the dialog that matters."""
    doc = policy(weapon={"unsafe": True, "prompts": ["a photo of a knife"]})
    assert find_conflicts(doc, "gore", ["a photo of a knife"], unsafe=True) == []


def test_a_rule_does_not_conflict_with_itself():
    doc = policy(rainbow={"unsafe": False, "prompts": ["a photo of rainbow"]})
    assert find_conflicts(doc, "rainbow", ["a photo of rainbow"], unsafe=False) == []


def test_exact_matching_alone_misses_the_real_case():
    """Pins the limit of the text-only pass, which is why find_conflicts takes a model_dir.

    Character matching is not how the clash arises. An admin writes an allowing rule, uploads
    a picture weeks later and describes it afresh; the two sentences agree in meaning and
    differ in wording. Measured, one extra article was enough to slip through - "a photo of a
    rainbow" against "a photo of rainbow" went undetected - so the guard existed and never
    fired on the case it was built for. With a model_dir the semantic pass catches all of
    these at 0.92-0.99; without one, this is what remains.
    """
    doc = policy(rainbow={"unsafe": False, "prompts": ["a photo of rainbow"]})
    assert find_conflicts(doc, "unrest", ["a photo of a rainbow"], unsafe=True) == []
    assert find_conflicts(doc, "unrest", ["a rainbow photo"], unsafe=True) == []


def test_the_similarity_bar_sits_in_a_measured_gap():
    """PHRASE_SAME_MIN is not a feel. Measured against "a photo of rainbow", the ways a person
    rephrases it score 0.923-0.990 and unrelated subjects 0.743-0.844. The bar belongs in the
    empty stretch between, and moving it outside that range means re-measuring first."""
    from app.admin_policy import PHRASE_SAME_MIN
    assert 0.845 < PHRASE_SAME_MIN < 0.922


def test_resolving_removes_the_losing_copy_only():
    doc = policy(
        rainbow={"unsafe": False, "prompts": ["a photo of rainbow", "a colourful sky"]},
        unrest={"unsafe": True, "prompts": ["a photo of a riot"]},
    )
    found = find_conflicts(doc, "unrest", ["a photo of rainbow"], unsafe=True)
    _resolve_conflicts(doc, found, keep="refuse")
    # The allowing rule survives, minus the contested sentence; its other wording is untouched.
    assert doc["labels"]["rainbow"]["prompts"] == ["a colourful sky"]
    assert doc["labels"]["unrest"]["prompts"] == ["a photo of a riot"]


def test_a_rule_emptied_by_resolution_is_removed():
    """A rule with no wordings matches nothing - a row in the policy file pretending to be a
    decision. It goes, rather than sitting there looking live."""
    doc = policy(rainbow={"unsafe": False, "prompts": ["a photo of rainbow"]})
    found = find_conflicts(doc, "unrest", ["a photo of rainbow"], unsafe=True)
    _resolve_conflicts(doc, found, keep="refuse")
    assert "rainbow" not in doc["labels"]


def test_resolving_the_other_way_changes_nothing():
    """Keeping the EXISTING verdict must leave the policy exactly as it was: the caller drops
    the new phrasing instead, so nothing here should be touched."""
    doc = policy(rainbow={"unsafe": False, "prompts": ["a photo of rainbow"]})
    found = find_conflicts(doc, "unrest", ["a photo of rainbow"], unsafe=True)
    _resolve_conflicts(doc, found, keep="allow")
    assert doc["labels"]["rainbow"]["prompts"] == ["a photo of rainbow"]


@pytest.mark.parametrize("blank", ["", "   ", "!!!"])
def test_blank_wordings_never_conflict(blank):
    doc = policy(rainbow={"unsafe": False, "prompts": [blank]})
    assert find_conflicts(doc, "unrest", [blank], unsafe=True) == []
