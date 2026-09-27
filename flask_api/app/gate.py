"""Layer 1 deterministic gate: config-driven pre-filter run before the model.

Rule kinds (``kind`` in gate_patterns.yaml):

    pattern   (default) regex ``patterns`` and literal ``terms``, matched anywhere
    words     literal ``terms`` matched on whole words/phrases; scales to large word lists
    targeted  a ``subjects`` word (e.g. he/she/they) followed later in the SAME sentence
              by one of the ``terms``: abuse aimed at a person or business

``terms_file`` (one path or a list) adds terms from newline-separated files; missing
files are skipped, so large or sensitive lists can live outside git. ``exclude_file``
and ``exclude`` remove file terms that have innocent meanings (e.g. "flange", "hoe").
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
_WHITESPACE = re.compile(r"\s+")
_TOKEN = re.compile(r"\w+(?:['-]\w+)*")
_CLEAN_TERM = re.compile(r"\w+(?:['-]\w+)*(?: \w+(?:['-]\w+)*)*")
_SENTENCE_END = re.compile(r"(?<!\d)[.!?]+|[.!?]+(?!\d)")  # "2.5 kg" is not a break; "costs 50. Call" is
_ACTIONS = ("block", "flag")
_KINDS = ("pattern", "words", "targeted")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_APOSTROPHES)
    text = _INVISIBLE.sub("", text)
    return _WHITESPACE.sub(" ", text).casefold().strip()


def sentences(norm: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(norm) if s.strip()]


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
        self.odd =(re.compile(r"(?<!\w)(?:" + "|".join(sorted(odd, key=len, reverse=True)) + r")(?!\w)")
                    if odd else None)

    def __len__(self) -> int:
        return len(self.phrases) + (len(self.odd.pattern.split("|")) if self.odd else 0)

    def _hit_at(self, tokens: list[str], i: int) -> bool:
        tok = tokens[i]
        if (tok,) in self.phrases:
            return True
        if len(tok) > 3 and tok.endswith("es") and (tok[:-2],) in self.phrases:
            return True
        if len(tok) > 2 and tok.endswith("s") and (tok[:-1],) in self.phrases:
            return True
        if tok not in self.starts:
            return False
        return any(tuple(tokens[i:i + n]) in self.phrases for n in range(2, self.max_len + 1))

    def search(self, text: str) -> bool:
        tokens = _TOKEN.findall(text)
        if any(self._hit_at(tokens, i) for i in range(len(tokens))):
            return True
        return bool(self.odd and self.odd.search(text))


def _subject_index(tokens: list[str], subjects: frozenset[str]) -> int | None:
    for i, tok in enumerate(tokens):
        if tok in subjects or tok.split("'")[0] in subjects:  # "he's", "they're"
            return i
    return None


def targeted_matcher(subjects: frozenset[str], terms: TermSet) -> Callable[[str], bool]:
    def match(norm: str) -> bool:
        for sentence in sentences(norm):
            tokens = _TOKEN.findall(sentence)
            first = _subject_index(tokens, subjects)
            if first is None:
                continue
            after = tokens[first + 1:]
            if any(terms._hit_at(after, i) for i in range(len(after))):
                return True
            if terms.odd:
                pos = re.search(r"(?<!\w)" + re.escape(tokens[first]) + r"(?!\w)", sentence)
                if pos and terms.odd.search(sentence, pos.end()):
                    return True
        return False
    return match


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    action: str
    kind: str
    matcher: Callable[[str], bool]


@dataclass(frozen=True)
class GateMatch:
    rule_id: str
    category: str
    action: str

    def as_dict(self) -> dict[str, str]:
        return {"rule_id": self.rule_id, "category": self.category, "action": self.action}


@dataclass(frozen=True)
class GateResult:
    matches: tuple[GateMatch, ...]

    @property
    def blocked(self) -> bool:
        return any(m.action == "block" for m in self.matches)

    @property
    def flagged(self) -> bool:
        return any(m.action == "flag" for m in self.matches)


class Gate:
    def __init__(self, rules: list[Rule], version: int | str = "unversioned"):
        # Block rules first so a block short-circuits before cheaper-to-skip flag rules.
        self.rules = sorted(rules, key=lambda r: r.action != "block")
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
        norm = normalize(text)
        matches: list[GateMatch] = []
        for rule in self.rules:
            if rule.matcher(norm):
                matches.append(GateMatch(rule.id, rule.category, rule.action))
                if rule.action == "block":
                    break
        return GateResult(tuple(matches))


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
        return Rule(rule_id, category, action, kind, term_set.search)

    if kind == "targeted":
        subjects = frozenset(normalize(s) for s in _as_list(spec.get("subjects")))
        if not subjects:
            raise ValueError(f"gate rule {rule_id}: targeted rules need 'subjects'")
        term_set = TermSet(terms)
        if not len(term_set):
            log.info("gate rule %s has no terms, inactive", rule_id)
            return None
        log.info("gate rule %s: %d subjects, %d terms", rule_id, len(subjects), len(term_set))
        return Rule(rule_id, category, action, kind, targeted_matcher(subjects, term_set))

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
    return Rule(rule_id, category, action, kind, regex.search)
