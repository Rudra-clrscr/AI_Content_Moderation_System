"""A visual refusal must name the thing that refused it.

A cloud photograph was refused and the record said this:

    label: safe (0.4633)        probe: 0.588 against a bar of 0.70
    rule_id: visual.safe

Every number in it reads as a picture that should have been published, and the rule named was
the SAFE one - the label that happened to score highest, which has no bearing on why anything
was refused. The actual cause was an exemplar: a rainbow somebody had marked "refuse things
like this" weeks earlier, matching at 0.828, because at the image level rainbows and clouds
are both soft-coloured sky.

Nothing in the result pointed anywhere near that. The admin reasonably concluded the probe was
at fault, deselected the picture and retrained - which could not help, because the probe was
not the one objecting. The diagnosis took scoring the image by hand against each channel in
turn.

So these pin the two things that would have made it a thirty-second job: the result carries
the exemplar's score and id, and the audit row names the channel and the image to go and
change rather than an unrelated label.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _Config:
    exemplar_reject_min: float = 0.60
    probe_reject_min: float = 0.70
    reject_min: float = 0.90


def _result(**kw):
    from app.clip import VisualResult
    base = dict(checked=True, label="safe", category="", score=0.4633, unsafe_score=0.05,
                scores={"safe": 0.4633, "rainbow": 0.4114})
    base.update(kw)
    return VisualResult(**base)


def test_an_exemplar_refusal_names_the_exemplar():
    """The id is the point: it is the row in the dashboard to go and change."""
    r = _result(exemplar_block=0.8283,
                exemplar_match={"id": 3666, "category": "rainbow", "score": 0.8283})
    d = r.as_dict()["visual"]
    assert d["exemplar"]["block"] == 0.8283
    assert d["exemplar"]["matched"]["id"] == 3666
    assert d["exemplar"]["matched"]["category"] == "rainbow"


def test_refused_by_names_the_channel_that_actually_decided():
    cfg = _Config()
    # Exemplar alone: the probe is below its bar and no label clears its own.
    assert _result(exemplar_block=0.83, probe_unsafe=0.588,
                   probe_scores={"safe": 0.4}).refused_by(cfg) == "exemplar"
    # Probe alone.
    assert _result(probe_unsafe=0.75, probe_scores={"weapon": 0.75}).refused_by(cfg) == "probe"
    # Nothing objects.
    assert _result(probe_unsafe=0.2, probe_scores={"safe": 0.8}).refused_by(cfg) is None


def test_the_exemplar_channel_is_reported_even_when_the_probe_also_fires():
    """Both can clear their bars at once. The name reported has to be the one that DECIDED,
    which is the exemplar, because decides_reject checks it first - otherwise the record
    sends an operator to retrain a probe that was not the cause."""
    cfg = _Config()
    r = _result(exemplar_block=0.83, probe_unsafe=0.72, probe_scores={"weapon": 0.72})
    assert r.refused_by(cfg) == "exemplar"


def test_an_allow_exemplar_overrides_and_reports_no_refusal():
    """Marking a picture "allow things like this" is how an admin carves out one kind of
    picture without deleting the rule refusing the rest, so it has to win."""
    cfg = _Config()
    r = _result(exemplar_block=0.83, exemplar_allow=1.0)
    assert r.refused_by(cfg) is None


def test_nothing_is_reported_when_the_check_did_not_run():
    from app.clip import VisualResult
    assert VisualResult(checked=False).refused_by(_Config()) is None
    assert "visual" not in VisualResult(checked=False).as_dict()


def test_a_result_with_no_exemplar_match_omits_the_block():
    """Absent, not zero: every picture has some similarity to something, and a field present
    on every result is one more number to read past on the ones that matter."""
    d = _result().as_dict()["visual"]
    assert "exemplar" not in d
