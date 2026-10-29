"""A helper called from two screens must not be declared inside one of them.

THE BUG THIS COMES FROM. An admin clicked "Allow like this" on a grid of religious symbols
filed under `safe`. The card showed "Applying…" and never changed - no dialog, no error, no
message, for ever.

The server had done exactly the right thing. That picture scores 0.933 against the `religion`
rule, over its 0.9 bar, so marking it ALLOW contradicts a written rule that refuses it;
admin_policy.set_exemplars answers 409 `exemplar_conflict` precisely so the admin can be asked
which should stand. The dialog that asks, conflictModal, was declared inside policyView. The
two call sites on the images screen - the teach buttons on a card, and an upload whose
description contradicts a rule - therefore threw ReferenceError from inside an async click
handler, where nothing catches it. Both of the backend's conflict guards were unreachable, and
the symptom was a spinner.

admin.js is one IIFE holding a dozen screen functions, so this is a whole class of bug and
nothing in the build would report it: there is no bundler, no module system and no linter in
the serving path - the file is handed to the browser as it sits.

This reads the file structurally rather than running it, since the project has no JS test
harness and this needs none: the question is only which function body a declaration sits in.
"""
from __future__ import annotations

import re
from pathlib import Path

ADMIN_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "articles" / "admin.js"

# The file's own convention: the single IIFE's own functions sit at four spaces, and anything
# declared inside one of those - a helper belonging to that one screen - is indented further.
VIEW = re.compile(r"^    function (\w+)\s*\(")
NESTED = re.compile(r"^\s{6,}function (\w+)\s*\(")


def _lines() -> list[str]:
    return ADMIN_JS.read_text(encoding="utf-8").split("\n")


def _view_spans(lines: list[str]) -> dict[str, tuple[int, int]]:
    """{view name: [first line, last line)} for every function at shared scope."""
    found = [(i, m.group(1)) for i, l in enumerate(lines) if (m := VIEW.match(l))]
    return {name: (i, found[n + 1][0] if n + 1 < len(found) else len(lines))
            for n, (i, name) in enumerate(found)}


def test_conflict_dialog_is_reachable_from_every_screen_that_raises_one():
    """The specific regression: both servers raise 409s that only this dialog can answer."""
    lines = _lines()
    decls = [i for i, l in enumerate(lines) if re.match(r"^\s*function conflictModal\s*\(", l)]
    assert len(decls) == 1, "conflictModal should be declared exactly once"
    assert VIEW.match(lines[decls[0]]), (
        f"conflictModal is declared at line {decls[0] + 1} inside another function. It is "
        "called from policyView AND imagesOptionView, so nesting it makes one of those a "
        "ReferenceError thrown inside an async click handler - silent, and it strands the "
        "control that was waiting on it.")


def test_no_screen_calls_a_helper_nested_in_a_different_screen():
    """The general form, so the next one is caught by the build rather than by an admin."""
    lines = _lines()
    spans = _view_spans(lines)
    assert "imagesOptionView" in spans and "policyView" in spans, "admin.js layout changed"

    offences = []
    for i, line in enumerate(lines):
        m = NESTED.match(line)
        if not m:
            continue
        name = m.group(1)
        owner = next((v for v, (a, b) in spans.items() if a <= i < b), None)
        if owner is None:
            continue
        for caller, (a, b) in spans.items():
            if caller == owner:
                continue
            # A screen that declares its OWN function of that name is not reaching across;
            # the two simply share a name, as policyView and logsView both do with `render`.
            if any(re.match(rf"^\s*function {name}\s*\(", lines[j]) for j in range(a, b)):
                continue
            hits = [j + 1 for j in range(a, b)
                    if re.search(rf"(?<![\w.]){name}\s*\(", lines[j])
                    and not lines[j].strip().startswith("//")]
            if hits:
                offences.append(f"{name}() is declared inside {owner} (line {i + 1}) but "
                                f"called from {caller} at line(s) {hits}")

    assert not offences, (
        "A helper is being called from a screen that cannot see it. In admin.js that is a "
        "ReferenceError at click time, and inside an async handler it is silent:\n  "
        + "\n  ".join(offences))
