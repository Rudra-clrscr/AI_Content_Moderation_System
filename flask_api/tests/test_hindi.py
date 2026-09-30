"""Hindi and Hinglish: tokenizing, sentence splitting, and the Layer 1 rules.

A QA pass on 2026-09-30 found that Devanagari abuse and threats were allowed on every
dashboard surface (scores 0.02-0.05), and Hinglish only about half the time. The model is
English-centric, so Layer 1 carries these until a multilingual model ships. Two things had
to be fixed underneath the rules first:

* Python's ``\\w`` skips the combining marks Indic scripts write vowels with, so "तुम"
  tokenized as ["त", "म"] - no Devanagari term or subject could match reliably.
* The Devanagari danda (।) was not a sentence end, so a Hindi paragraph reached the model
  as one diluted block and the "same sentence" test in targeted rules spanned all of it.

The cases here are the regression set from that QA report.
"""
import pytest

from app.config import PROJECT_ROOT
from app.gate import Gate, normalize, sentences
from app.targeted import scan_spans

GATE_FILE = PROJECT_ROOT / "config" / "gate_patterns.yaml"


@pytest.fixture(scope="module")
def gate():
    return Gate.from_yaml(GATE_FILE)


# --------------------------------------------------------------------------- tokenizing

@pytest.mark.parametrize("text,expected", [
    ("तुम सब बेवकूफ हो", ["तुम", "सब", "बेवकूफ", "हो"]),
    ("मैं तुम्हें जान से मार दूंगा", ["मैं", "तुम्हें", "जान", "से", "मार", "दूंगा"]),
    ("hello world", ["hello", "world"]),
    ("don't-stop now", ["don't-stop", "now"]),
])
def test_words_are_whole_words_in_every_script(text, expected):
    from app.gate import _TOKEN
    assert [m.group() for m in _TOKEN.finditer(text)] == expected


def test_danda_ends_a_sentence():
    assert sentences(normalize("तुम सब बेवकूफ हो। हमारी कंपनी अच्छी है।")) == \
        ["तुम सब बेवकूफ हो", " हमारी कंपनी अच्छी है"]


def test_danda_splits_the_sentence_scan_too():
    text = "तुम सब बेवकूफ हो। हमारी कंपनी अच्छी है।"
    assert [text[s:e] for s, e in scan_spans(text)] == \
        ["तुम सब बेवकूफ हो", "हमारी कंपनी अच्छी है"]


def test_decimals_and_domains_still_are_not_sentence_breaks():
    assert sentences(normalize("price 2.5 kg from example.com. he said 50. ok")) == \
        ["price 2.5 kg from example.com", " he said 50", " ok"]


# --------------------------------------------------------------------------- what must be blocked

@pytest.mark.parametrize("text,rule", [
    # Devanagari
    ("तुम सब बेवकूफ हो।", "abuse.hi_targeted"),
    ("मैं तुम्हें जान से मार दूंगा, अपनी दुकान बंद कर दो वरना जला दूंगा।", "threat.violence_hi"),
    ("साले कमीने लोग, भाग जाओ यहाँ से।", "abuse.hi_blocklist"),
    ("यह कंपनी घटिया और धोखेबाज है, सब चोर हैं।", "abuse.hi_targeted"),
    ("मैं तुम सबको जान से मार दूंगा, दुकान जला दूंगा।", "threat.violence_hi"),
    # Roman-script Hindi (Hinglish), including mixed with English
    ("Tum chor ho, rate kam karo warna tumhe jaan se maar denge.", "abuse.hi_targeted"),
    ("Saale haramkhor log, tum sab bewakoof aur kamine ho.", "abuse.hi_blocklist"),
    ("Main tumhe jaan se maar dunga, dukaan band kar de warna jala dunga.", "threat.violence_hi"),
    ("Tumhari dukaan jala denge, jaan se maar dunga sabko.", "threat.violence_hi"),
    ("Ye company bekaar hai aur manager bewakoof hai.", "abuse.hi_targeted"),
])
def test_hindi_abuse_and_threats_are_blocked(gate, text, rule):
    r = gate.check(text)
    assert r.blocked, f"not blocked: {text}"
    assert r.matches[0].rule_id == rule


def test_blocked_span_points_at_the_abuse(gate):
    text = "Ye company bekaar hai aur manager bewakoof hai."
    [match] = [m for m in gate.check(text).matches if m.action == "block"]
    assert [text[s:e] for s, e in match.spans] == ["bewakoof"]


# --------------------------------------------------------------------------- what must not be

@pytest.mark.parametrize("text", [
    # Ordinary Hindi business writing.
    "हमारी कंपनी छोटे व्यवसायों को भरोसेमंद आपूर्तिकर्ता खोजने में मदद करती है।",
    "सूरत में सूती साड़ियाँ बनाते हैं और पूरे भारत में भेजते हैं।",
    "हमें 500 किलो बासमती चावल चाहिए, कृपया अपना रेट भेजें।",
    "आप सब का स्वागत है, हमारी सेवाएं पूरे गुजरात में उपलब्ध हैं।",
    "Aap humein WhatsApp par rate bhej sakte hain, hum kal tak order confirm karenge.",
    "Bhai rate kam karo to hum 1000 piece ka order de denge.",
    "Sab log time par payment karte hain, hamara record accha hai.",
    # The words in abuse.hi_targeted have ordinary meanings when nothing is aimed at anyone.
    "Chor Bazaar ke paas humara godown hai, Mumbai mein.",
    "गधा गाड़ी के लिए मजबूत पहिये, 40 किलो क्षमता।",
    "Gadha gaadi ke wheels, 40 kg capacity, Jaipur se.",
    # "maar" and "jala" are ordinary verbs, which is why the threat rule needs whole phrases.
    "यह मशीन खराब हो गई थी, मोटर जला दिया था, अब ठीक है।",
    "Machine band kar ke fuse jala diya tha, ab theek hai.",
    # Fraud-awareness writing, the false-positive class the English rules were tuned against.
    "ठगी से कैसे बचें - अगर कोई पहले पैसे मांगे तो सावधान रहें।",
    "धोखाधड़ी की शिकायत कैसे दर्ज करें, यह लेख समझाता है।",
])
def test_ordinary_hindi_is_not_blocked(gate, text):
    assert not gate.check(text).blocked, f"false positive: {text}"


def test_hindi_rules_are_active_in_the_shipped_config(gate):
    assert {"abuse.hi_blocklist", "abuse.hi_targeted", "threat.violence_hi"} <= {r.id for r in gate.rules}
