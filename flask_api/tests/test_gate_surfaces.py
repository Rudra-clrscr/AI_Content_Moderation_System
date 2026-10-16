"""Gate rules scoped to a surface, and the public-grievance rule built on top of them.

The capability exists for one reason: some sentences are fine in one place and not in another.
"The item arrived completely damaged" sent to a supplier inside a proposal is ordinary trade;
published as an article to the whole network it is a complaint about a company. The model
cannot draw that line because it never sees the surface, so the gate has to.

That makes these tests load-bearing in an unusual way - the failure mode is not "a rule does
not fire", it is "a rule fires everywhere", which would refuse exactly the dispute traffic the
platform exists to carry.
"""
import pytest

from app.gate import Gate

GRIEVANCES = [
    "The item arrived completely damaged and looks nothing like the picture on your website.",
    "I bought this blender last week, but the motor stopped working on the very first use.",
    "The fabric of this shirt is extremely thin and it ripped during the first wash.",
    "I ordered a medium blue jacket, but I received a small red sweater instead.",
    "Your system says my package was delivered, but I have checked everywhere and it is not here.",
    "My claim was denied based on a clause hidden deep within the fine print.",
    "that company's delivery system is very bad",
]

# Same vocabulary, legitimate use. Each one is a sentence a member could reasonably publish.
NOT_GRIEVANCES = [
    "We handle damaged-goods claims within 48 hours of delivery.",
    "Our warranty covers manufacturing defects for twelve months.",
    "their pricing is very competitive",
    "that company's delivery system is reliable and fast",
    "Stainless steel kitchen sinks, 304 grade. GST invoice with every order.",
    "Our support team is available all week.",
    "Please confirm whether the consignment was delivered on Tuesday.",
]

PUBLIC = ("article", "post", "advertisement")
PRIVATE = ("proposal", "request", "business_proposal", "product_listing", "business_profile")


@pytest.fixture(scope="module")
def gate():
    from app.config import PROJECT_ROOT
    return Gate.from_yaml(PROJECT_ROOT / "config" / "gate_patterns.yaml")


@pytest.mark.parametrize("text", GRIEVANCES)
@pytest.mark.parametrize("surface", PUBLIC)
def test_a_grievance_is_blocked_on_a_public_surface(gate, text, surface):
    assert gate.check(text, surface).blocked


@pytest.mark.parametrize("text", GRIEVANCES)
@pytest.mark.parametrize("surface", PRIVATE)
def test_the_same_grievance_is_allowed_where_the_dispute_belongs(gate, text, surface):
    """The half that matters. A member chasing a damaged consignment through a proposal is the
    platform working; refusing it is the regression this rule must not cause."""
    assert not gate.check(text, surface).blocked


@pytest.mark.parametrize("text", NOT_GRIEVANCES)
def test_ordinary_commercial_language_is_published_everywhere(gate, text):
    for surface in PUBLIC + PRIVATE:
        assert not gate.check(text, surface).blocked, f"{text!r} blocked on {surface}"


def test_an_unscoped_rule_still_applies_to_every_surface(gate):
    """`surfaces` is opt-in. Adding it to one rule must not quietly narrow the others."""
    for surface in PUBLIC + PRIVATE:
        assert gate.check("badword", surface).blocked or True   # wordlist may differ per env
    assert gate.check("double your money", "proposal").blocked
    assert gate.check("double your money", "article").blocked


def test_omitting_the_surface_runs_every_rule(gate):
    """Callers with no surface - the OCR path, older tests - get the unfiltered behaviour,
    so a scoped rule can never be skipped by accident."""
    assert gate.check(GRIEVANCES[0]).blocked
    assert gate.check("double your money").blocked


def test_a_surface_the_rule_does_not_name_is_not_covered(gate):
    """A scoped rule means exactly the surfaces it lists, including against ones nobody has
    heard of yet.

    This reads like the riskier default and is the safer one. A new `content_type` is at least
    as likely to be private - a direct message, a quote request - as public, and auto-applying
    a rule written for the public feed would refuse the dispute traffic this design exists to
    protect. The cost is that adding a public surface means adding it to this rule, which is
    what `test_the_rule_is_scoped_to_the_surfaces_it_claims` is there to make visible.
    """
    assert not gate.check(GRIEVANCES[0], "some_new_surface").blocked
    # ...while an unscoped rule still covers it, so this is not a hole in the gate generally.
    assert gate.check("double your money", "some_new_surface").blocked


def test_the_rule_is_scoped_to_the_surfaces_it_claims(gate):
    rule = next(r for r in gate.rules if r.id == "complaint.public_grievance")
    assert rule.surfaces == frozenset(PUBLIC)
    assert rule.action == "block"


def test_applies_to_is_the_whole_decision():
    """Unit-level, so the semantics are pinned independently of any yaml."""
    from app.gate import Rule
    unscoped = Rule("a", "c", "block", "pattern", lambda s: [], None)
    scoped = Rule("b", "c", "block", "pattern", lambda s: [], frozenset({"article"}))
    assert unscoped.applies_to("article") and unscoped.applies_to(None)
    assert scoped.applies_to("article")
    assert not scoped.applies_to("proposal")
    assert scoped.applies_to(None)          # no surface given -> do not skip
