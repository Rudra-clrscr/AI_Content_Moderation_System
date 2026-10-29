"""A rule created in the dashboard must say what it will tell the author.

THE BUG THIS COMES FROM. An admin added a visual rule for posters carrying abusive language.
They named it `abuses`, left the "Reported category" box empty, and saved. The box defaults to
the rule name, so the policy got `category: abuses` - beside the `abuse` that already existed
and already read "abusive language". Nothing on the screen mentioned either fact.

Two consequences, neither visible at the time:

  * The readable name an author is shown lives in config/feedback_messages.yaml, which this
    flow does not touch. feedback.py fell back to the raw string, so a refused upload was told
    "It appears to break BONC community policies on abuses".

  * Scores are a softmax over every phrasing in the file, so the allowing list is what the
    refusing ones are measured against. Three refusing phrasings went in and none allowing,
    taking the policy from 82-against-81 to 85-against-88.

Three of the four rules admins have added this way were noticed and hand-named later. `abuses`
was not, and the only thing that ever reported it was a unit test, days afterwards, naming
neither the admin nor what they had done.

Like test_policy_conflicts, these exercise the arithmetic rather than the rebuild, so they
need neither the 254 MB text encoder nor a writable policy file.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.admin_policy import _consequences, _near_categories, _prompt_balance  # noqa: E402
from app.feedback import Feedback  # noqa: E402

# As config/feedback_messages.yaml has them, which is what makes `abuses` beside `abuse` the
# case it was.
NAMED = {"abuse": "abusive language", "weapons": "weapons",
         "minors": "pictures of children", "parties": "parties and celebrations"}
FB = Feedback(categories=NAMED)


def policy(**labels) -> dict:
    return {"version": 1, "labels": labels}


def rule(category, n=1, unsafe=True) -> dict:
    e = {"unsafe": unsafe, "prompts": [f"a photo of subject {i}" for i in range(n)]}
    if unsafe:
        e["category"] = category
    return e


# ---- the author-facing guarantee -----------------------------------------------------

def test_a_curated_name_is_preferred():
    """A written name beats any derived one: `minors` reads as "pictures of children", which
    no amount of string work gets to."""
    assert FB.category_name("minors") == "pictures of children"


def test_an_unmapped_category_is_tidied_rather_than_published_raw():
    """The exact string that was reaching authors: policies on physique_display."""
    bare = Feedback(categories={})
    assert bare.category_name("physique_display") == "physique display"
    assert bare.category_name("violence_or_unrest") == "violence or unrest"


def test_a_category_with_nothing_in_it_still_reads_as_english():
    """Exemplar refusals are filed under the picture's own dataset category, which is whatever
    the database holds - including, for a row that never got one, nothing at all."""
    bare = Feedback(categories={})
    assert bare.category_name("") == "content"
    assert bare.category_name(None) == "content"


def test_the_reject_message_uses_the_tidied_name():
    """End to end through build(), because the fallback used to live inline at that one call
    site and a second caller would have reintroduced it."""
    from app.gate import GateMatch, GateResult
    from app.routing import Decision

    gate = GateResult(matches=[GateMatch(rule_id="visual.physique", category="physique_display",
                                         action="block", spans=((0, 1),))])
    fb = Feedback(categories={}, reject_message="policies on {categories}.")
    out = fb.build(Decision.REJECT, gate, [], "post", 0.9)
    assert "physique display" in out["message"]
    assert "physique_display" not in out["message"]


# ---- minting a category next to one that already exists ------------------------------

def test_a_plural_of_an_existing_category_is_spotted():
    """The reported bug exactly: `abuses` created beside the `abuse` that already meant it."""
    assert _near_categories("abuses", NAMED) == {"abuse": "abusive language"}


def test_a_singular_of_an_existing_category_is_spotted():
    assert _near_categories("weapon", NAMED) == {"weapons": "weapons"}
    assert _near_categories("minor", NAMED) == {"minors": "pictures of children"}
    assert _near_categories("party", NAMED) == {"parties": "parties and celebrations"}


def test_separators_do_not_hide_a_near_match():
    assert _near_categories("abuse_s", NAMED) == {"abuse": "abusive language"}


def test_an_unrelated_category_matches_nothing():
    """A question asked about everything is a question nobody reads. `unrest` must not be
    offered `violence_or_unrest`, or the dialog becomes noise."""
    assert _near_categories("loud_hailing", NAMED) == {}
    assert _near_categories("unrest", NAMED) == {}
    assert _near_categories("", NAMED) == {}


def test_a_category_that_already_exists_is_not_offered_itself():
    assert _near_categories("abuse", NAMED) == {}


# ---- what a save reports -------------------------------------------------------------

def test_a_save_says_what_the_author_will_be_told():
    doc = policy(knives=rule("weapons"), safe=rule(None, n=9, unsafe=False))
    c = _consequences(doc, "knives", FB)
    assert c["author_sees"] == "weapons"
    assert c["category_curated"] is True
    assert "naming_hint" not in c


def test_a_new_category_is_reported_as_unnamed():
    """The step that was missing. The save is allowed - only the admin knows what they meant -
    but they are told, at the moment they can still change it."""
    doc = policy(loudhailer=rule("loud_hailing"), safe=rule(None, n=9, unsafe=False))
    c = _consequences(doc, "loudhailer", FB)
    assert c["category_curated"] is False
    assert c["author_sees"] == "loud hailing"
    assert "loud_hailing" in c["naming_hint"]
    assert "feedback_messages.yaml" in c["naming_hint"]


def test_a_near_duplicate_is_reported_as_a_question():
    doc = policy(abuses=rule("abuses"), safe=rule(None, n=9, unsafe=False))
    c = _consequences(doc, "abuses", FB)
    assert c["similar_categories"] == {"abuse": "abusive language"}
    assert "Did you mean" in c["naming_hint"]
    assert "abusive language" in c["naming_hint"]


def test_a_rule_with_no_category_reports_the_name_it_defaulted_to():
    """The editor's box defaults to the rule name, so this is the shape the bug arrived in."""
    doc = policy(abuses={"unsafe": True, "prompts": ["a poster with indian abuses"]},
                 safe=rule(None, n=9, unsafe=False))
    c = _consequences(doc, "abuses", FB)
    assert c["category"] == "abuses"
    assert c["similar_categories"] == {"abuse": "abusive language"}


def test_an_allowed_rule_is_not_asked_about_categories():
    """A safe label refuses nothing, so it tells no author anything."""
    doc = policy(pottery=rule(None, n=3, unsafe=False), safe=rule(None, n=9, unsafe=False))
    c = _consequences(doc, "pottery", FB)
    assert "author_sees" not in c
    assert "naming_hint" not in c
    assert c["safe_prompts"] == 12


# ---- the other invisible consequence -------------------------------------------------

def test_the_balance_counts_phrasings_not_rules():
    """One rule with fourteen wordings outweighs four rules with one each, and it is the
    wordings that enter the softmax."""
    doc = policy(manhandling=rule("weapons", n=14), safe=rule(None, n=3, unsafe=False))
    b = _prompt_balance(doc)
    assert (b["unsafe_prompts"], b["safe_prompts"], b["safe_margin"]) == (14, 3, -11)


def test_a_comfortable_margin_raises_nothing():
    doc = policy(knives=rule("weapons", n=2), safe=rule(None, n=20, unsafe=False))
    assert "warning" not in _prompt_balance(doc)


def test_an_equal_margin_raises_nothing():
    """82 against 81 is what ships. The line is "fewer than", not "not comfortably more"."""
    doc = policy(knives=rule("weapons", n=5), safe=rule(None, n=5, unsafe=False))
    assert "warning" not in _prompt_balance(doc)


def test_tipping_the_balance_is_reported():
    doc = policy(knives=rule("weapons", n=6), safe=rule(None, n=5, unsafe=False))
    b = _prompt_balance(doc)
    assert b["safe_margin"] == -1
    assert "refusing phrasings against" in b["warning"]
    # And it must keep pointing at the measurement rather than prescribing a fix: adding
    # allowing phrasings was measured and traded four wrong refusals for eight correct ones.
    assert "eval_clip_images.py" in b["warning"]


def test_the_warning_travels_with_the_save_that_caused_it():
    """Reported by _consequences too, not only on the policy screen: the admin who tipped it
    is the one who can still decide otherwise."""
    doc = policy(knives=rule("weapons", n=6), safe=rule(None, n=5, unsafe=False))
    assert "warning" in _consequences(doc, "knives", FB)


# ---- the same bug through the exemplar door -------------------------------------------
# An exemplar refusal carries the category of the picture an admin MARKED, which is the
# dataset folder it was filed under - a different vocabulary from the one the policy
# publishes. `physique` reached the author verbatim where the policy would have said
# "personal physique photos", and a mark on a picture from the allowed set said "safe".

def test_a_marked_pictures_folder_is_translated_to_its_policy_category():
    from app.clip import VisualCheck
    from app.config import load_settings

    v = load_settings().visual
    if not v.ready:
        import pytest as _p
        _p.skip("the image model is not present in this checkout")
    assert isinstance(v, VisualCheck)
    assert v.policy_category("physique") == "physique_display"
    assert v.policy_category("minor") == "minors"
    assert v.policy_category("unrest") == "violence_or_unrest"


def test_a_folder_that_names_no_policy_translates_to_nothing():
    """`toy`, `cloud` and the allowed set are real folders an admin can mark a picture in,
    and none of them is a policy area. Inventing one is the bug, not the fix."""
    from app.config import load_settings

    v = load_settings().visual
    if not v.ready:
        import pytest as _p
        _p.skip("the image model is not present in this checkout")
    assert v.policy_category("toy") == ""
    assert v.policy_category("safe") == ""
    assert v.policy_category("") == ""


def test_a_blank_category_gets_the_generic_wording():
    """So a refusal with no policy area to name says so, instead of naming the wrong one."""
    from app.gate import GateMatch, GateResult
    from app.routing import Decision

    gate = GateResult(matches=[GateMatch(rule_id="visual.exemplar#7", category="",
                                         action="block", spans=())])
    fb = Feedback(categories=NAMED, reject_message="policies on {categories}.",
                  reject_message_generic="It breaks our community policies.")
    out = fb.build(Decision.REJECT, gate, [], "post", 0.9)
    assert out["message"] == "It breaks our community policies."
    assert out["categories"] == []


def test_a_named_category_alongside_a_blank_one_is_still_named():
    """Two blocking matches, one of them an exemplar with nothing to name: the one that does
    have an area must still publish it."""
    from app.gate import GateMatch, GateResult
    from app.routing import Decision

    gate = GateResult(matches=[
        GateMatch(rule_id="visual.exemplar#7", category="", action="block", spans=()),
        GateMatch(rule_id="gate.slur", category="abuse", action="block", spans=((0, 4),)),
    ])
    fb = Feedback(categories=NAMED, reject_message="policies on {categories}.")
    out = fb.build(Decision.REJECT, gate, [], "post", 0.9)
    assert out["message"] == "policies on abusive language."
    assert out["categories"] == ["abuse"]
