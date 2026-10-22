"""The sentence scan must score the sentence the author wrote, full stop included.

REPORTED. "a person like that man needs to be properly trained." published inside an article
at the strictest moderation level. The level was being applied correctly and the model was
not at fault - it scores that sentence 0.8544 on its own. The scan was scoring something
else.

`sentence_spans` ends a span at the break, so the terminator is not part of it. Those offsets
are what the author sees highlighted and are deliberate - `test_sentence_spans_are_trimmed`
pins them, and widening them broke sixteen other tests about where a highlight falls. But the
TEXT handed to the model is a separate question, and it was being taken from the same narrow
slice: the whole-document pass always saw the full stop and the sentence scan never did.

Usually they agree. Over 120 sentences from res/, dropping the terminator moves the score by
a median of 0.0000, a maximum of 0.0284, and across a bar not once. The exceptions are the
point, because the scan exists for them:

    'a person like that man needs to be properly trained'     0.0118
    'a person like that man needs to be properly trained.'    0.8544

So a sentence the model refuses on its own travelled through a longer article untouched -
precisely what a per-sentence scan is for.

These tests pin the seam rather than the model: the text that reaches the scorer, and the
offsets that come back out. The model's own sensitivity to one character is a training
matter and is not something this can fix.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.model import ScoreResult  # noqa: E402
from app.pipeline import _scored_end, _span_scores  # noqa: E402


class Recorder:
    """Stands in for the scorer and remembers exactly what it was asked to score."""

    def __init__(self):
        self.seen: list[str] = []

    def many(self, texts):
        self.seen.extend(texts)
        return [ScoreResult(0.1, "safe", {"safe": 0.9, "violation": 0.1}, "rec-1", 0.0)
                for _ in texts]


# ---- what reaches the model --------------------------------------------------------------

def test_the_scorer_is_given_the_sentence_with_its_full_stop():
    content = "Our depot opened. a person like that man needs to be properly trained. Thanks."
    #          0123456789...
    start = content.index("a person")
    end = content.index(" Thanks") - 1          # as sentence_spans would report it: no full stop
    assert content[start:end] == "a person like that man needs to be properly trained"

    rec = Recorder()
    _span_scores(content, content, [(start, end)], rec)
    assert rec.seen == ["a person like that man needs to be properly trained."]


def test_the_offsets_handed_back_are_the_ones_passed_in():
    """The span is not widened - only the reading of it. Every caller zips these results
    against the spans it passed, and an author sees those offsets highlighted."""
    content = "Alpha beta gamma. Delta epsilon zeta."
    spans = [(0, 16), (18, 36)]
    rec = Recorder()
    out = _span_scores(content, content, spans, rec)
    assert len(out) == len(spans)
    assert content[spans[0][0]:spans[0][1]] == "Alpha beta gamma"


def test_a_question_mark_and_an_exclamation_count_too():
    for text, want in (("Is that so? Next.", "Is that so?"),
                       ("Stop that! Next.", "Stop that!")):
        rec = Recorder()
        _span_scores(text, text, [(0, text.index(" Next"))], rec)
        assert rec.seen == [want]


def test_an_ellipsis_is_taken_whole():
    text = "Well then... Next."
    rec = Recorder()
    _span_scores(text, text, [(0, 9)], rec)
    assert rec.seen == ["Well then..."]


def test_a_devanagari_danda_counts():
    text = "यह गलत है। अगला।"
    end = text.index("।")
    rec = Recorder()
    _span_scores(text, text, [(0, end)], rec)
    assert rec.seen == [text[:end] + "।"]


# ---- where there is nothing to add -------------------------------------------------------

def test_a_sentence_with_no_terminator_is_unchanged():
    text = "the last line has no full stop"
    rec = Recorder()
    _span_scores(text, text, [(0, len(text))], rec)
    assert rec.seen == [text]


def test_a_span_ending_at_a_newline_gains_nothing():
    """A newline breaks a sentence but is not punctuation, and a title must not acquire the
    body's first full stop."""
    text = "Warehouse update\nWe ship weekly."
    rec = Recorder()
    _span_scores(text, text, [(0, 16)], rec)
    assert rec.seen == ["Warehouse update"]


def test_a_comma_is_not_a_terminator():
    text = "first part, second part."
    rec = Recorder()
    _span_scores(text, text, [(0, 10)], rec)
    assert rec.seen == ["first part"]


def test_scored_end_stops_at_the_end_of_the_text():
    assert _scored_end("abc.", 3) == 4
    assert _scored_end("abc", 3) == 3
    assert _scored_end("", 0) == 0


# ---- the de-obfuscated view follows the same slice ----------------------------------------

def test_the_deobfuscated_view_is_read_over_the_same_span():
    """A span is scored twice when the de-obfuscated view reads differently. Both readings
    have to cover the same characters, or the comparison is between two different sentences."""
    content = "buy fr0m us or else. ok."
    view = "buy from us or else. ok."
    rec = Recorder()
    _span_scores(content, view, [(0, 19)], rec)
    assert rec.seen == ["buy fr0m us or else.", "buy from us or else."]


def test_identical_view_is_not_scored_twice():
    text = "nothing obfuscated here. ok."
    rec = Recorder()
    _span_scores(text, text, [(0, 23)], rec)
    assert rec.seen == ["nothing obfuscated here."]
