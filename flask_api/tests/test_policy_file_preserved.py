"""Saving the policy must not destroy the policy file.

config/visual_policy.yaml is 484 lines of which 249 are comments, and the comments are the
reasoning: why `minor` sits at 0.85 when `unrest` sits at 0.70, which image set each bar was
measured on, what the `safe` list is for and why a thin one makes ordinary photographs look
suspicious. None of it is recoverable from the data.

The first version of the admin policy editor wrote the file with yaml.safe_dump, which
round-trips the data and throws everything else away. One save cut the file from 484 lines to
176 and nobody noticed until the diff was read - the policy still worked, so the only symptom
was that the next person to touch a threshold would have no idea why it was where it was.

So saves edit the text in place: a new phrasing inserts a line, a verdict replaces one, a new
rule is appended, a deleted one is cut. These pin that, including the round trip - four
operations that cancel out must leave the file byte-identical, because a save that drifts even
in whitespace turns every policy change into an unreadable diff.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.admin_policy import _render_preserving, edit_policy_text  # noqa: E402

POLICY = ROOT / "config" / "visual_policy.yaml"
pytestmark = pytest.mark.skipif(not POLICY.exists(), reason="no visual_policy.yaml")


def original() -> str:
    return POLICY.read_text(encoding="utf-8")


def comments(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip().startswith("#"))


def test_the_file_under_test_is_actually_documented():
    """If this ever fails the others prove nothing - they would be preserving nothing."""
    assert comments(original()) > 100


def test_adding_a_phrasing_keeps_every_comment():
    text = original()
    out = edit_policy_text(text, "weapon", add_prompt="a photo of a crossbow")
    assert comments(out) == comments(text)
    assert len(out.splitlines()) == len(text.splitlines()) + 1
    assert "a photo of a crossbow" in yaml.safe_load(out)["labels"]["weapon"]["prompts"]


def test_flipping_a_verdict_changes_one_line():
    text = original()
    out = edit_policy_text(text, "wedding", set_unsafe=False)
    assert comments(out) == comments(text)
    assert len(out.splitlines()) == len(text.splitlines())
    assert yaml.safe_load(out)["labels"]["wedding"]["unsafe"] is False


def test_a_new_rule_does_not_disturb_the_existing_ones():
    text = original()
    out = edit_policy_text(text, "rainbow",
                           new_entry={"unsafe": False, "prompts": ["a photo of rainbow"]})
    assert comments(out) == comments(text)
    before, after = yaml.safe_load(text)["labels"], yaml.safe_load(out)["labels"]
    assert set(after) - set(before) == {"rainbow"}
    for name in before:
        assert after[name] == before[name]


def test_four_operations_that_cancel_leave_the_file_byte_identical():
    """The strongest form of the claim. A save that drifts even in whitespace makes every
    policy change an unreadable diff, which is how the comment loss went unnoticed."""
    text = original()
    out = edit_policy_text(text, "weapon", add_prompt="a photo of a crossbow")
    out = edit_policy_text(out, "rainbow",
                           new_entry={"unsafe": False, "prompts": ["a photo of rainbow"]})
    out = edit_policy_text(out, "rainbow", delete=True)
    out = edit_policy_text(out, "weapon", remove_prompt="a photo of a crossbow")
    assert out == text


def test_the_differ_reaches_the_same_result_as_hand_edits():
    """The callers hand over a whole document, so the edits are worked out by diffing. This
    checks that route, not only the primitives underneath it."""
    text = original()
    doc = yaml.safe_load(text)
    new = yaml.safe_load(text)
    new["labels"]["weapon"]["prompts"] = list(new["labels"]["weapon"]["prompts"]) + ["a photo of a crossbow"]
    out = _render_preserving(text, doc, new)
    assert comments(out) == comments(text)
    assert yaml.safe_load(out)["labels"]["weapon"]["prompts"][-1] == "a photo of a crossbow"


def test_the_differ_falls_back_loudly_rather_than_losing_the_policy():
    """A policy that refuses to save is worse than one saved plainly, so an edit this cannot
    express still writes - via safe_dump, with a warning. The data must survive even then."""
    text = original()
    doc = yaml.safe_load(text)
    new = {"version": 1, "labels": {"only": {"unsafe": True, "prompts": ["a photo of a thing"]}}}
    out = _render_preserving(text, doc, new)
    assert yaml.safe_load(out)["labels"]["only"]["prompts"] == ["a photo of a thing"]
