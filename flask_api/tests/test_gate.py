import pytest

from app.gate import Gate, normalize


@pytest.fixture
def gate(gate_file):
    return Gate.from_yaml(gate_file)


def test_clean_text_passes(gate):
    r = gate.check("Wholesale steel pipes, ISO certified, ships from Pune.")
    assert not r.blocked and not r.flagged and r.matches == ()


def test_term_blocks_case_insensitive(gate):
    r = gate.check("This is a BadWord here")
    assert r.blocked
    assert r.matches[0].rule_id == "test.block_term"


def test_terms_are_word_bounded(gate):
    assert not gate.check("notabadwordish").blocked


def test_multiword_term_survives_extra_whitespace(gate):
    assert gate.check("two \n\t  words").blocked


def test_zero_width_chars_do_not_evade(gate):
    assert gate.check("bad​word").blocked


def test_fullwidth_chars_normalised(gate):
    assert gate.check("ｂａｄｗｏｒｄ").blocked


def test_regex_pattern_blocks(gate):
    assert gate.check("Invest now and DOUBLE   your money!").blocked


def test_flag_does_not_block(gate):
    r = gate.check("see bit.ly/abc123 for details")
    assert r.flagged and not r.blocked


def test_block_short_circuits_after_first_block(gate):
    r = gate.check("badword double your money")
    assert [m.action for m in r.matches] == ["block"]


def test_normalize():
    assert normalize("  Ｈello​   WORLD ") == "hello world"


def test_invalid_action_rejected(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text("rules:\n  - id: x\n    action: delete\n    patterns: ['a']\n")
    with pytest.raises(ValueError, match="action"):
        Gate.from_yaml(p)


def test_invalid_regex_reports_rule(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text("rules:\n  - id: broken\n    patterns: ['(unclosed']\n")
    with pytest.raises(ValueError, match="broken"):
        Gate.from_yaml(p)


def test_missing_terms_file_skips_rule(tmp_path):
    p = tmp_path / "cfg" / "g.yaml"
    p.parent.mkdir()
    p.write_text("rules:\n  - id: t\n    terms_file: nope/missing.txt\n")
    assert Gate.from_yaml(p).rules == []


def test_terms_file_loaded(tmp_path):
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "t.txt").write_text("# comment\nfoo\n\nbar baz\n")
    p = tmp_path / "cfg" / "g.yaml"
    p.parent.mkdir()
    p.write_text("rules:\n  - id: t\n    terms_file: private/t.txt\n")
    g = Gate.from_yaml(p)
    assert g.check("x bar baz y").blocked
    assert not g.check("comment").blocked


def test_shipped_config_loads_every_rule():
    from app.config import PROJECT_ROOT
    g = Gate.from_yaml(PROJECT_ROOT / "config" / "gate_patterns.yaml")
    assert len(g.rules) == 6  # no rule may be silently inactive


@pytest.mark.parametrize("text,rule", [
    ("Our supplier is a scumbag, never order from them", "abuse.blocklist"),
    ("Verify your seller account at paypa1-verify.example today", "spam.blacklisted_domain"),
])
def test_shipped_demo_terms_block(text, rule):
    from app.config import PROJECT_ROOT
    r = Gate.from_yaml(PROJECT_ROOT / "config" / "gate_patterns.yaml").check(text)
    assert r.blocked and r.matches[0].rule_id == rule


@pytest.mark.parametrize("text", [
    "Visit fast-cash-bonanza.examples.com",
    "Ask about our go-to hellfire chilli sauce",
])
def test_shipped_demo_terms_are_word_bounded(text):
    from app.config import PROJECT_ROOT
    assert not Gate.from_yaml(PROJECT_ROOT / "config" / "gate_patterns.yaml").check(text).matches
