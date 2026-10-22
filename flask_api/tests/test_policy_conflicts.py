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

from app.admin_policy import (_norm_phrase, _resolve_conflicts, find_conflicts,  # noqa: E402
                              prompts_this_edit_introduces)


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


# ---- flipping a verdict is the path that asks the question ----------------------------

def test_turning_a_rule_off_is_what_raises_the_question():
    """The route the admin actually took. "Stop refusing" flips one rule's verdict, which is
    exactly when its wordings start meaning the opposite of a rule that kept theirs - so this
    is the press that most often needs the question asked, not the rarest.

    It reached the admin as a red sentence ending "Choose which one should stand", on a page
    offering no choice. The server had sent the clashing rules all along; the handler for that
    button printed the message and dropped them.
    """
    doc = policy(
        physique={"unsafe": True, "prompts": ["a photo of a bodybuilder flexing"]},
        revealing={"unsafe": True, "prompts": ["a photo of a bodybuilder flexing",
                                               "a glamour photo in revealing clothes"]},
    )
    # While it keeps refusing, nothing to ask: both rules agree.
    assert find_conflicts(doc, "physique", doc["labels"]["physique"]["prompts"],
                          unsafe=True) == []

    # Flip it to allow and the same wording now means both things at once.
    clash = find_conflicts(doc, "physique", doc["labels"]["physique"]["prompts"],
                           unsafe=False)
    assert len(clash) == 1
    assert clash[0]["other_label"] == "revealing"
    assert clash[0]["other_verdict"] == "refuse"
    assert clash[0]["this_verdict"] == "allow"


def test_the_payload_carries_what_the_dialog_needs_to_render():
    """The dialog shows the wording, the rule it clashes with and each side's verdict. If any
    of those stops being sent the dialog renders blank rows, which is worse than the banner
    it replaced - it looks answerable and is not."""
    doc = policy(
        a={"unsafe": True, "prompts": ["a photo of a rainbow"]},
        b={"unsafe": False, "prompts": ["a photo of a rainbow"]},
    )
    for c in find_conflicts(doc, "a", ["a photo of a rainbow"], unsafe=True):
        assert c["phrasing"] and c["other_label"] and c["other_verdict"] and c["this_verdict"]


def test_resolving_a_flip_leaves_one_verdict_standing():
    """End to end on the rule arithmetic: flip, resolve, and the wording means one thing."""
    doc = policy(
        physique={"unsafe": False, "prompts": ["a photo of a bodybuilder flexing"]},
        revealing={"unsafe": True, "prompts": ["a photo of a bodybuilder flexing",
                                               "a glamour photo in revealing clothes"]},
    )
    clash = find_conflicts(doc, "physique", doc["labels"]["physique"]["prompts"], unsafe=False)
    _resolve_conflicts(doc, clash, keep="allow")
    assert doc["labels"]["physique"]["prompts"] == ["a photo of a bodybuilder flexing"]
    # Stripped from the rule that held the other verdict, and that rule still has its own.
    assert doc["labels"]["revealing"]["prompts"] == ["a glamour photo in revealing clothes"]


# ---- only ask about what the edit introduces ------------------------------------------

def test_changing_a_threshold_asks_nothing():
    """Four of the fifteen shipped rules - minor, romantic, unrest and safe - overlap the
    `safe` rule closely enough to trip the similarity bar, and have shipped that way from the
    start. Checking every wording on every save made those four uneditable: changing a
    reject_min asked the admin to settle a contradiction they had not made and could only
    clear by deleting a wording they had not written."""
    previous = {"unsafe": True, "prompts": ["a portrait photograph of a child"],
                "reject_min": 0.85}
    entry = {"unsafe": True, "prompts": ["a portrait photograph of a child"],
             "reject_min": 0.75}
    assert prompts_this_edit_introduces(previous, entry) == []


def test_an_added_wording_is_the_only_thing_asked_about():
    """The rule keeps its verdict and its old wordings. Only the new line can introduce a
    contradiction, so only the new line is checked."""
    previous = {"unsafe": True, "prompts": ["a photo of a pistol"]}
    entry = {"unsafe": True, "prompts": ["a photo of a pistol", "a photo of a rainbow"]}
    assert prompts_this_edit_introduces(previous, entry) == ["a photo of a rainbow"]


def test_flipping_the_verdict_puts_every_wording_back_in_question():
    """The "Stop refusing" press. Nothing is added, but every wording the rule holds now
    means the opposite of what it meant, so all of them are checked again."""
    previous = {"unsafe": True, "prompts": ["a photo of a pistol", "a photo of a rifle"]}
    entry = {"unsafe": False, "prompts": ["a photo of a pistol", "a photo of a rifle"]}
    assert prompts_this_edit_introduces(previous, entry) == ["a photo of a pistol",
                                                             "a photo of a rifle"]


def test_a_brand_new_rule_is_new_all_through():
    assert prompts_this_edit_introduces(None, {"unsafe": True, "prompts": ["a", "b"]}) == ["a", "b"]


def test_rewording_counts_as_adding():
    """Punctuation and case are not a change; different words are. Otherwise an admin could
    slip a contradicting wording past the check by retyping it with a capital letter."""
    previous = {"unsafe": True, "prompts": ["A Photo of a Pistol."]}
    same = {"unsafe": True, "prompts": ["a photo of a pistol"]}
    assert prompts_this_edit_introduces(previous, same) == []

    reworded = {"unsafe": True, "prompts": ["a photo of a handgun"]}
    assert prompts_this_edit_introduces(previous, reworded) == ["a photo of a handgun"]


def test_removing_a_wording_introduces_nothing():
    previous = {"unsafe": True, "prompts": ["a photo of a pistol", "a photo of a rifle"]}
    entry = {"unsafe": True, "prompts": ["a photo of a pistol"]}
    assert prompts_this_edit_introduces(previous, entry) == []
