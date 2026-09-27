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
    ids = {r.id for r in g.rules}
    # Every rule with committed patterns/terms must be active; only profanity.wordlist
    # depends solely on the downloaded (gitignored) word lists.
    assert ids >= {"abuse.blocklist", "abuse.targeted", "spam.blacklisted_domain", "scam.advance_fee",
                   "spam.url_shortener", "payment.off_platform", "contact.messenger_redirect"}
    lists = PROJECT_ROOT / "config" / "private" / "wordlists"
    assert ("profanity.wordlist" in ids) == any(lists.glob("*.txt"))


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


def test_sentence_split_handles_numbers():
    from app.gate import sentences
    assert sentences("price 2.5 kg. he said 50. ok") == ["price 2.5 kg", " he said 50", " ok"]


@pytest.mark.parametrize("text", [
    "his thinking and mindset shameless",
    "her behaviour is disgraceful and despicable",
    "their owner is a crook",
    "they are cheats, avoid them",
    "he is a liar and a fraudster",
    "their manager is a con artist",
    "she is sick-minded",
    "he keeps cheating customers",
])
def test_shipped_character_insults_aimed_at_someone_block(text):
    from app.config import PROJECT_ROOT
    r = Gate.from_yaml(PROJECT_ROOT / "config" / "gate_patterns.yaml").check(text)
    assert r.blocked and r.matches[0].rule_id == "abuse.targeted"


@pytest.mark.parametrize("text", [
    "He found the test results disturbing and recalled the batch.",
    "They handle toxic chemicals safely and follow all regulations.",
    "They made a cheat sheet for installers.",
    "Their evil-eye bracelets are handmade in Jaipur.",
    "He caught the thief at our warehouse and called the police.",
    "Their prices are shamelessly low this week, grab the deal.",
    "They reported a corrupt file in the upload, please resend.",
    "We warn buyers about scammers. They should only pay through BONC.",
])
def test_shipped_insult_words_have_no_innocent_false_blocks(text):
    from app.config import PROJECT_ROOT
    assert not Gate.from_yaml(PROJECT_ROOT / "config" / "gate_patterns.yaml").check(text).blocked
