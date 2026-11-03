"""An exemplar has to be closer than the ALLOWING marks, not just close in absolute terms.

A scan of an ordinary supplier profile was refused like this:

    best label: safe (0.7007)
    exemplar block 0.6162   allow 0.5904   match #3672 (parties)

The picture was 0.616 similar to a party photograph somebody had marked "refuse things like
this", and 0.590 similar to something marked "allow things like this" — which is to say it
looked no more like one than the other. The bar was 0.60, the block side cleared it by
0.016, and a supplier's own paperwork was told it broke community policy.

That is not a bad mark so much as a bar asking the wrong question. 0.60 sits only just above
the 0.42–0.44 floor that unrelated pictures score against each other, so a mark with no
distinctive look clears it constantly: measured over 513 pictures, mark #3672 alone produced
45 of the 85 exemplar refusals, spread evenly across weapons, gore, safe objects and real
listings. A mark that matches everything discriminates nothing.

So the question became "is it nearer to something we refuse than to something we publish?".
These pin that, and pin the two implementations of it agreeing — `decides_reject` decides and
`refused_by` names the cause, separately, and a divergence means an audit row that credits a
channel which did not decide.
"""
from __future__ import annotations

import pytest

from app.clip import VisualCheck, VisualConfig, VisualResult


def check(margin=0.10, bar=0.60):
    return VisualCheck(VisualConfig(enabled=True, exemplar_reject_min=bar,
                                    exemplar_margin_min=margin))


def result(block, allow, **kw):
    """A result whose ONLY possible cause is the exemplar channel: top label safe, probe quiet.
    So anything that refuses it refused on the exemplar, and nothing else can mask the rule."""
    base = dict(checked=True, label="safe", category="", score=0.70, unsafe_score=0.05,
                scores={"safe": 0.70}, exemplar_block=block, exemplar_allow=allow,
                exemplar_match={"id": 3672, "category": "parties", "score": block})
    base.update(kw)
    return VisualResult(**base)


# ---- the case that took PDF moderation out of the deploy -------------------------------

def test_the_supplier_profile_scan_is_not_refused():
    """The real numbers. 0.6162 clears the 0.60 bar; it beats the allowing mark by 0.026."""
    assert check().decides_reject(result(0.6162, 0.5904)) is False


def test_and_it_is_not_reported_as_a_refusal_either():
    assert result(0.6162, 0.5904).refused_by(check().config) is None


@pytest.mark.parametrize("name,block,allow", [
    ("blank page", 0.6945, 0.6203),
    ("price list", 0.6668, 0.6078),
    ("supplier profile", 0.6338, 0.5863),
    ("spec sheet", 0.6338, 0.5901),
    ("letter", 0.6235, 0.5433),
])
def test_ordinary_business_documents_publish(name, block, allow):
    """Six of eight documents rendered as scans were refused before this. A blank page was
    the strongest match of the set, which is the clearest possible sign that what the mark
    had found was "flat and pale", not "a party"."""
    assert check().decides_reject(result(block, allow)) is False, name


# ---- but a mark an admin meant still works ---------------------------------------------

def test_a_picture_that_really_does_look_like_the_mark_is_still_refused():
    """The mechanism has to survive. A genuine look-alike sits far closer to the marked
    picture than to anything in the allowed set — 0.83 against 0.46 here — and that gap is
    exactly what the margin preserves."""
    assert check().decides_reject(result(0.83, 0.46)) is True
    assert result(0.83, 0.46).refused_by(check().config) == "exemplar"


def test_the_margin_is_the_only_thing_that_changed():
    """Same block score, same bar; only the distance to the allowing mark differs."""
    c = check()
    assert c.decides_reject(result(0.65, 0.60)) is False   # 0.05 apart
    assert c.decides_reject(result(0.65, 0.50)) is True    # 0.15 apart


def test_an_allow_mark_over_the_bar_still_overrides_outright():
    """Unchanged: an admin saying "allow things like this" wins before any of this is read."""
    assert check().decides_reject(result(0.95, 0.96)) is False


def test_zero_restores_the_old_behaviour():
    """The escape hatch for anyone who wants the absolute bar back."""
    assert check(margin=0.0).decides_reject(result(0.6162, 0.5904)) is True


def test_a_config_without_the_field_behaves_as_it_did():
    """An older bundle's config, or a stub in someone else's test, has no margin field.
    `refused_by` must not raise on it — it falls back to the bar alone."""
    class Old:
        exemplar_reject_min = 0.60
        probe_reject_min = 0.70
        reject_min = 0.90

    assert result(0.6162, 0.5904).refused_by(Old()) == "exemplar"


# ---- the two implementations must not drift ---------------------------------------------

@pytest.mark.parametrize("block", [0.0, 0.45, 0.59, 0.60, 0.62, 0.70, 0.83, 0.99])
@pytest.mark.parametrize("allow", [0.0, 0.45, 0.58, 0.60, 0.65, 0.95])
@pytest.mark.parametrize("margin", [0.0, 0.10])
def test_decides_reject_and_refused_by_agree(block, allow, margin):
    """`decides_reject` returns the verdict and `refused_by` names the cause, each with its
    own copy of the rule. When they disagree the picture is refused and the record credits
    the wrong channel — or clears it and the record names a refusal that never happened.

    Only the exemplar channel can fire on these results, so `refused_by` naming anything is
    exactly `decides_reject` saying True.
    """
    c = check(margin=margin)
    r = result(block, allow)
    assert (r.refused_by(c.config) == "exemplar") is c.decides_reject(r), (
        f"block={block} allow={allow} margin={margin}: "
        f"decides_reject={c.decides_reject(r)} refused_by={r.refused_by(c.config)}")
