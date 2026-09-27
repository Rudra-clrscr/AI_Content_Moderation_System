"""Layer 1 deterministic gate: config-driven pre-filter run before the model.

Rule actions (``action`` in gate_patterns.yaml):

    block     reject immediately; the model is skipped
    revise    fixable problem: the author is asked to edit the highlighted text;
              the model still runs and can still reject
    flag      recorded in the result for the audit log only; doesn't change the decision

Rule kinds (``kind``):

    pattern   (default) regex ``patterns`` and literal ``terms``, matched anywhere
    words     literal ``terms`` matched on whole words/phrases; scales to large word lists
    targeted  a ``subjects`` word (e.g. he/she/they) followed later in the SAME sentence
              by one of the ``terms``: abuse aimed at a person or business

``terms_file`` (one path or a list) adds terms from newline-separated files; missing
files are skipped, so large or sensitive lists can live outside git. ``exclude_file``
and ``exclude`` remove file terms that have innocent meanings (e.g. "flange", "hoe").

Every match carries character spans into the ORIGINAL text, so the author can be
shown exactly what to change.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

log = logging.getLogger(__name__)

# Zero-width / invisible characters commonly used to dodge keyword filters.
_INVISIBLE = re.compile("[​-‏⁠-⁤﻿­]")
_APOSTROPHES = str.maketrans({"‘": "'", "’": "'"})
_TOKEN = re.compile(r"\w+(?:['-]\w+)*")
_CLEAN_TERM = re.compile(r"\w+(?:['-]\w+)*(?: \w+(?:['-]\w+)*)*")
_SENTENCE_END = re.compile(r"(?<!\d)[.!?]+|[.!?]+(?!\d)")  # "2.5 kg" is not a break; "costs 50. Call" is
_ACTIONS = ("block", "revise", "flag")
_KINDS = ("pattern", "words", "targeted")

Span = tuple[int, int]


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Normalise for matching, and map each normalised character back to its index in `text`.

    Per character: NFKC (full-width letters -> ASCII), curly -> straight apostrophes,
    casefold; invisible characters dropped; whitespace runs collapsed to one space;
    leading/trailing space removed.
    """
    chars: list[str] = []
    index: list[int] = []
    last_space = True
    for i, ch in enumerate(text):
        if _INVISIBLE.match(ch):
            continue
        for c in unicodedata.normalize("NFKC", ch).translate(_APOSTROPHES).casefold():
            if c.isspace():
                if last_space:
                    continue
                c, last_space = " ", True
            else:
                last_space = False
            chars.append(c)
            index.append(i)
    if chars and chars[-1] == " ":
        chars.pop()
        index.pop()
    return "".join(chars), index


def normalize(text: str) -> str:
    return normalize_with_map(text)[0]


def sentence_spans(norm: str) -> list[Span]:
    spans, start = [], 0
    for m in _SENTENCE_END.finditer(norm):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(norm)))
    return [(s, e) for s, e in spans if norm[s:e].strip()]


def sentences(norm: str) -> list[str]:
    return [norm[s:e] for s, e in sentence_spans(norm)]


class TermSet:
    """Literal words and phrases, matched on whole tokens.

    A single-word term also matches its plural ("idiot" matches "idiots").
    Terms containing symbols (e.g. "s&m") can't be tokenized reliably, so they
    fall back to a word-bounded regex.
    """

    def __init__(self, terms: list[str]):
        self.phrases: set[tuple[str, ...]] = set()
        odd: list[str] = []
        for term in terms:
            t = normalize(term)
            if not t:
                continue
            if _CLEAN_TERM.fullmatch(t):
                self.phrases.add(tuple(t.split(" ")))
            else:
                odd.append(re.escape(t))
        self.max_len = max((len(p) for p in self.phrases), default=0)
        self.starts = {p[0] for p in self.phrases if len(p) > 1}  # only these can begin a phrase
        self.odd = (re.compile(r"(?<!\w)(?:" + "|".join(sorted(odd, key=len, reverse=True)) + r")(?!\w)")
                    if odd else None)

    def __len__(self) -> int:
        return len(self.phrases) + (len(self.odd.pattern.split("|")) if self.odd else 0)

    def _match_len(self, tokens: list[str], i: int) -> int:
        """Number of tokens matched starting at tokens[i] (0 = no match)."""
        tok = tokens[i]
        if (tok,) in self.phrases:
            return 1
        if len(tok) > 3 and tok.endswith("es") and (tok[:-2],) in self.phrases:
            return 1
        if len(tok) > 2 and tok.endswith("s") and (tok[:-1],) in self.phrases:
            return 1
        if tok not in self.starts:
            return 0
        for n in range(min(self.max_len, len(tokens) - i), 1, -1):
            if tuple(tokens[i:i + n]) in self.phrases:
                return n
        return 0

    def find(self, text: str, start: int = 0, end: int | None = None) -> list[Span]:
        end = len(text) if end is None else end
        toks = list(_TOKEN.finditer(text, start, end))
        words = [t.group() for t in toks]
        spans: list[Span] = []
        i = 0
        while i < len(words):
            n = self._match_len(words, i)
            if n:
                spans.append((toks[i].start(), toks[i + n - 1].end()))
                i += n
            else:
                i += 1
        if self.odd:
            spans += [m.span() for m in self.odd.finditer(text, start, end)]
        return sorted(spans)


def _subject_token(tokens: list[re.Match], subjects: frozenset[str]) -> int | None:
    for i, tok in enumerate(tokens):
        word = tok.group()
        if word in subjects or word.split("'")[0] in subjects:  # "he's", "they're"
            return i
    return None


def targeted_finder(subjects: frozenset[str], terms: TermSet) -> Callable[[str], list[Span]]:
    def find(norm: str) -> list[Span]:
        spans: list[Span] = []
        for s, e in sentence_spans(norm):
            tokens = list(_TOKEN.finditer(norm, s, e))
            first = _subject_token(tokens, subjects)
            if first is not None:
                spans += terms.find(norm, tokens[first].end(), e)
        return spans
    return find


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    action: str
    kind: str
    finder: Callable[[str], list[Span]]


@dataclass(frozen=True)
class GateMatch:
    rule_id: str
    category: str
    action: str
    spans: tuple[Span, ...] = ()   # offsets into the original text

    def as_dict(self) -> dict:
        return {"rule_id": self.rule_id, "category": self.category, "action": self.action,
                "spans": [list(s) for s in self.spans]}


@dataclass(frozen=True)
class GateResult:
    matches: tuple[GateMatch, ...]

    @property
    def blocked(self) -> bool:
        return any(m.action == "block" for m in self.matches)

    @property
    def needs_revision(self) -> bool:
        return any(m.action == "revise" for m in self.matches)

    @property
    def flagged(self) -> bool:
        return any(m.action == "flag" for m in self.matches)


class Gate:
    def __init__(self, rules: list[Rule], version: int | str = "unversioned"):
        # Block rules first so a block short-circuits before the others run.
        self.rules = sorted(rules, key=lambda r: _ACTIONS.index(r.action))
        self.version = version

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Gate":
        path = Path(path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        base = path.parent.parent  # project root, so terms_file paths are repo-relative
        rules = [r for spec in raw.get("rules", []) if (r := _compile_rule(spec, base))]
        log.info("gate loaded: %d active rules from %s", len(rules), path)
        return cls(rules, version=raw.get("version", "unversioned"))

    def check(self, text: str) -> GateResult:
        norm, index = normalize_with_map(text)
        matches: list[GateMatch] = []
        for rule in self.rules:
            found = [(s, e) for s, e in rule.finder(norm) if e > s]
            if found:
                spans = tuple(_trim(text, index[s], index[e - 1] + 1) for s, e in found)
                matches.append(GateMatch(rule.id, rule.category, rule.action, spans))
                if rule.action == "block":
                    break
        return GateResult(tuple(matches))


def _trim(text: str, start: int, end: int) -> Span:
    """Drop whitespace at the edges of a highlight."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _read_lines(rule_id: str, files, base: Path) -> list[str]:
    lines: list[str] = []
    for f in _as_list(files):
        path = Path(f) if Path(f).is_absolute() else base / f
        if path.exists():
            lines += [t.strip() for t in path.read_text(encoding="utf-8").splitlines()
                      if t.strip() and not t.lstrip().startswith("#")]
        else:
            log.info("gate rule %s: optional file %s not present", rule_id, path)
    return lines


def _compile_rule(spec: dict, base: Path) -> Rule | None:
    rule_id = spec["id"]
    action = spec.get("action", "block")
    kind = spec.get("kind", "pattern")
    if action not in _ACTIONS:
        raise ValueError(f"gate rule {rule_id}: action must be one of {_ACTIONS}, got {action!r}")
    if kind not in _KINDS:
        raise ValueError(f"gate rule {rule_id}: kind must be one of {_KINDS}, got {kind!r}")

    # Exclusions apply to file terms only: inline terms are chosen deliberately.
    excluded = {normalize(t) for t in _as_list(spec.get("exclude")) + _read_lines(rule_id, spec.get("exclude_file"), base)}
    file_terms = [t for t in _read_lines(rule_id, spec.get("terms_file"), base) if normalize(t) not in excluded]
    terms = list(spec.get("terms") or []) + file_terms
    category = spec.get("category", "uncategorized")

    if kind == "words":
        term_set = TermSet(terms)
        if not len(term_set):
            log.info("gate rule %s has no terms, inactive", rule_id)
            return None
        log.info("gate rule %s: %d terms", rule_id, len(term_set))
        return Rule(rule_id, category, action, kind, term_set.find)

    if kind == "targeted":
        subjects = frozenset(normalize(s) for s in _as_list(spec.get("subjects")))
        if not subjects:
            raise ValueError(f"gate rule {rule_id}: targeted rules need 'subjects'")
        term_set = TermSet(terms)
        if not len(term_set):
            log.info("gate rule %s has no terms, inactive", rule_id)
            return None
        log.info("gate rule %s: %d subjects, %d terms", rule_id, len(subjects), len(term_set))
        return Rule(rule_id, category, action, kind, targeted_finder(subjects, term_set))

    alternatives = list(spec.get("patterns") or [])
    if terms:
        escaped = sorted((re.escape(normalize(t)) for t in terms), key=len, reverse=True)
        alternatives.append(r"(?<!\w)(?:" + "|".join(escaped) + r")(?!\w)")
    if not alternatives:
        log.info("gate rule %s has no patterns/terms, inactive", rule_id)
        return None
    try:
        regex = re.compile("|".join(f"(?:{a})" for a in alternatives))
    except re.error as exc:
        raise ValueError(f"gate rule {rule_id}: invalid regex: {exc}") from exc
    return Rule(rule_id, category, action, kind, lambda norm: [m.span() for m in regex.finditer(norm)])
