"""Layer 1 deterministic gate: config-driven regex pre-filter run before the model."""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# Zero-width / invisible characters commonly used to dodge keyword filters.
_INVISIBLE = re.compile("[​-‏⁠-⁤﻿­]")
_WHITESPACE = re.compile(r"\s+")
_ACTIONS = ("block", "flag")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE.sub("", text)
    return _WHITESPACE.sub(" ", text).casefold().strip()


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    action: str
    regex: re.Pattern[str]


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
            if rule.regex.search(norm):
                matches.append(GateMatch(rule.id, rule.category, rule.action))
                if rule.action == "block":
                    break
        return GateResult(tuple(matches))


def _compile_rule(spec: dict, base: Path) -> Rule | None:
    rule_id = spec["id"]
    action = spec.get("action", "block")
    if action not in _ACTIONS:
        raise ValueError(f"gate rule {rule_id}: action must be one of {_ACTIONS}, got {action!r}")

    alternatives = list(spec.get("patterns") or [])
    terms = list(spec.get("terms") or [])
    if terms_file := spec.get("terms_file"):
        tf = Path(terms_file) if Path(terms_file).is_absolute() else base / terms_file
        if tf.exists():
            terms += [t.strip() for t in tf.read_text(encoding="utf-8").splitlines()
                      if t.strip() and not t.startswith("#")]
        else:
            log.warning("gate rule %s: terms_file %s not found, skipping those terms", rule_id, tf)
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
    return Rule(rule_id, spec.get("category", "uncategorized"), action, regex)
