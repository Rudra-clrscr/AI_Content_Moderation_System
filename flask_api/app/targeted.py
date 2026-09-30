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

# A sentence ends at . ! ? followed by whitespace (or the end), or at a newline. So "2.5 kg",
# "example.com", "catalogue.pdf" and "index.php?id=2" are not broken into orphan fragments
# ("com", "pdf") that the model would score without context; "costs 50. Call" still breaks.
_SENTENCE_BREAK = re.compile(r"[.!?]+(?=\s|$)|\n")
_WORD = re.compile(r"\w+")
MIN_SPAN_WORDS = 3   # shorter fragments are merged into a neighbouring sentence before scoring

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


def scan_spans(text: str, min_words: int = MIN_SPAN_WORDS) -> list[tuple[int, int]]:
    """Sentence spans for scoring on their own: like sentence_spans, but a fragment of fewer
    than `min_words` words ("Thanks.", a one-word heading, "Winnie.") is merged into the
    following sentence (or the previous one at the end). Scored alone, a lone word has no
    context and its risk is noise; merged, it's judged with the words around it."""
    spans = sentence_spans(text)
    out: list[list[int]] = []
    carry: int | None = None           # start of short fragments waiting to join the next sentence
    for s, e in spans:
        if len(_WORD.findall(text, s, e)) < min_words:
            carry = s if carry is None else carry
            continue
        out.append([carry if carry is not None else s, e])
        carry = None
    if carry is not None:
        if out:
            out[-1][1] = spans[-1][1]
        else:
            out.append([carry, spans[-1][1]])
    return [(s, e) for s, e in out]


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
                if len(_WORD.findall(segment)) >= MIN_SPAN_WORDS and segment.strip() != text.strip():
                    found.append((m.start(), seg_end))
            start = end + 1
            if len(found) == self.max_segments:
                break
        return found


_WORD_SPAN = re.compile(r"\S+")

# Leetspeak: a digit/symbol standing in for a letter *between letters* ("c0py", "w4tches", "R0lex",
# "0TP" at a word start followed by letters). Product codes ("330W", "A16", "i5", "CO2") don't match.
_LEET_CHARS = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"}
# At a word start only 0 @ $ count ("0TP", "@ccount"): "3pm", "5kg", "4pcs", "1st" are quantities.
_LEET_IN_WORD = re.compile(r"[A-Za-z][013457@$!]+[A-Za-z]|^[0@$][A-Za-z]{2}")


def deobfuscate(text: str) -> str:
    """`text` with leetspeak letters restored, character for character (same length, so every
    offset still points at the same place). Unchanged when there is no leetspeak."""
    chars = list(text)
    for m in _WORD_SPAN.finditer(text):
        word = m.group()
        if not _LEET_IN_WORD.search(word):
            continue
        for i, ch in enumerate(word):
            if ch not in _LEET_CHARS or (ch == "!" and i == len(word) - 1):   # trailing "!" is punctuation
                continue
            near = [c for c in (word[i - 1] if i else "", word[i + 1] if i + 1 < len(word) else "") if c.isalpha()]
            if i == 0 and ch not in "0@$":
                continue
            if near:
                sub = _LEET_CHARS[ch]
                chars[m.start() + i] = sub.upper() if all(c.isupper() for c in near) else sub
    return "".join(chars)


def word_spans(text: str, start: int = 0, end: int | None = None) -> list[tuple[int, int]]:
    """(start, end) of each whitespace-separated word in text[start:end], as offsets into `text`."""
    end = len(text) if end is None else end
    return [m.span() for m in _WORD_SPAN.finditer(text, start, end)]


def word_windows(text: str, start: int, end: int, size: int, stride: int) -> list[tuple[int, int]]:
    """Overlapping windows of `size` words over a long sentence (none if it's `size` words or fewer).

    Text with no full stops (a pasted run-on paragraph, a list with no punctuation) is one
    "sentence" to the splitter; the model would read it diluted, or truncated at 256 tokens.
    Windows let a harmful phrase in the middle be scored on its own.
    """
    words = word_spans(text, start, end)
    if len(words) <= size:
        return []
    out = []
    for i in range(0, len(words), stride):
        chunk = words[i:i + size]
        out.append((chunk[0][0], chunk[-1][1]))
        if i + size >= len(words):
            break
    return out


def drop_word(text: str, start: int, end: int) -> str:
    """`text` with the word at [start, end) removed (and one adjacent space)."""
    before, after = text[:start], text[end:]
    if before.endswith(" ") and (after.startswith(" ") or not after):
        before = before[:-1]
    return before + after


@dataclass(frozen=True)
class WordScan:
    """Word-by-word view of every span the model flags (scored above allow_max).

    Occlusion: for each word in a flagged sentence, the sentence is re-scored with that word
    removed. contribution = risk(sentence) - risk(sentence without the word). Words whose
    removal lowers the risk by at least `min_contribution` are the *trigger words*: they
    are returned in `word_scores` and highlighted, so the author knows exactly what to
    rewrite. It also guards decisions: a sentence flagged by no single word can still be
    rejected (the combination is harmful), but the author is then shown the whole sentence.

    Costs one extra inference per word, only for flagged spans (allowed posts pay nothing),
    batched in one call when the model is batch-invariant.
    """

    enabled: bool = True
    min_contribution: float = 0.10   # a word counts as a trigger if removing it drops the risk this much
    max_triggers: int = 5            # trigger words highlighted per sentence
    max_spans: int = 5               # flagged spans analysed per request, riskiest first
    max_words: int = 60              # longer spans are analysed over their first max_words words
    # This phase only produces highlights — it never changes the decision — so it is bounded
    # hard. The linear pre-filter ranks the words first (~0.1 ms each against ~11 ms for the
    # model), and only the most promising go to the model.
    max_candidates: int = 10         # words per span actually occluded with the model
    max_calls: int = 40              # ceiling on model calls for the whole word phase
    window_words: int = 40           # sentences longer than this are also scored in word windows
    window_stride: int = 20
    deobfuscate: bool = True         # also score a leetspeak-restored reading ("c0py" -> "copy")

    def __post_init__(self) -> None:
        if not 0.0 < self.min_contribution <= 1.0:
            raise ValueError("word_scan.min_contribution must be in (0, 1]")
        if self.window_stride < 1 or self.window_words < self.window_stride:
            raise ValueError("word_scan needs 1 <= window_stride <= window_words")
        if min(self.max_triggers, self.max_spans, self.max_words, self.max_candidates, self.max_calls) < 1:
            raise ValueError("word_scan limits must be at least 1")


@dataclass(frozen=True)
class SentenceScan:
    """Score every sentence on its own, so a harmful sentence can't hide inside a clean post.

    A scam sentence tacked onto a normal listing ("Handmade leather wallets and belts.
    Earn 50000 per week from home") can score ~0 as a whole. If ANY sentence scores at
    reject level on its own, the whole post is rejected. Sentences in the middle band
    don't escalate: ordinary short sentences often land there, so it would be too noisy.
    Each scanned sentence costs one model inference (INT8 models can't be batched
    without changing scores, so they're scored one at a time). Grouping sentences into
    larger windows was tried for long articles and rejected: a buried scam sentence gets
    diluted inside the window just as it does in the whole text.
    """

    enabled: bool = True
    max_sentences: int = 400   # covers a full 25,000-character article; each is ~10 ms
    # A middle-band sentence in an otherwise allowed post asks for a revision (that sentence
    # highlighted). Only meaningful when the model's "unsure" output isn't the default for
    # ordinary text, i.e. with calibrated label_weights (see settings.yaml).
    revise_on_middle: bool = True

    def __post_init__(self) -> None:
        if self.max_sentences < 1:
            raise ValueError("sentence_scan.max_sentences must be at least 1")
