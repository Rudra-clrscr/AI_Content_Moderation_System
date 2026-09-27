"""Targeted-abuse check: score the part of each sentence that talks about someone.

Comments like "Good prices overall. But he is a disgusting cheat and should rot."
can pass as a whole (the harmless part dilutes the score) while one sentence is
clearly abuse aimed at a person or business. For every sentence containing a
subject word (he, she, they, ...), the model scores the text from that word to
the end of the sentence. If any such segment scores at reject level, the whole
content is rejected.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_SENTENCE_BREAK = re.compile(r"(?<!\d)[.!?]+|[.!?]+(?!\d)|\n")  # "2.5 kg" is not a break; "costs 50. Call" is
_WORD = re.compile(r"\w+")

DEFAULT_SUBJECTS = ("he", "she", "his", "her", "him", "they", "them", "their")


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of each non-empty sentence in `text`, trimmed of surrounding whitespace."""
    spans, start = [], 0
    for m in list(_SENTENCE_BREAK.finditer(text)) + [None]:
        end = m.start() if m else len(text)
        s, e = start, end
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e > s and _WORD.search(text, s, e):
            spans.append((s, e))
        start = m.end() if m else len(text)
    return spans


@dataclass(frozen=True)
class TargetedAbuse:
    enabled: bool = True
    subjects: tuple[str, ...] = DEFAULT_SUBJECTS
    max_segments: int = 3          # caps extra inferences per request (latency)
    _pattern: re.Pattern = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.max_segments < 1:
            raise ValueError("targeted_abuse.max_segments must be at least 1")
        words = "|".join(re.escape(s) for s in sorted(self.subjects, key=len, reverse=True))
        object.__setattr__(self, "_pattern", re.compile(rf"(?<!\w)(?:{words})(?!\w)", re.IGNORECASE))

    def segments(self, text: str) -> list[tuple[int, int]]:
        """(start, end) offsets into `text`: from the first subject word to the end of its sentence.

        Skips segments with nothing after the subject word, and a segment that is
        the whole text (the whole text has already been scored).
        """
        found: list[tuple[int, int]] = []
        start = 0
        breaks = [m.start() for m in _SENTENCE_BREAK.finditer(text)] + [len(text)]
        for end in breaks:
            m = self._pattern.search(text, start, end)
            if m:
                seg_end = len(text[:end].rstrip())
                segment = text[m.start():seg_end]
                if len(_WORD.findall(segment)) >= 2 and segment.strip() != text.strip():
                    found.append((m.start(), seg_end))
            start = end + 1
            if len(found) == self.max_segments:
                break
        return found


@dataclass(frozen=True)
class SentenceScan:
    """Score every sentence on its own, so a harmful sentence can't hide inside a clean post.

    A scam sentence tacked onto a normal listing ("Handmade leather wallets and belts.
    Earn 50000 per week from home") can score ~0 as a whole. If ANY sentence scores at
    reject level on its own, the whole post is rejected. Sentences in the middle band
    don't escalate: ordinary short sentences often land there, so it would be too noisy.
    Each scanned sentence costs one model inference (INT8 models can't be batched
    without changing scores, so they're scored one at a time).
    """

    enabled: bool = True
    max_sentences: int = 8

    def __post_init__(self) -> None:
        if self.max_sentences < 1:
            raise ValueError("sentence_scan.max_sentences must be at least 1")
