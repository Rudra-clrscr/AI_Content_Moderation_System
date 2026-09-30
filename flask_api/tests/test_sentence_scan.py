import pytest

from app import create_app
from app.model import ScoreResult
from app.targeted import SentenceScan, TargetedAbuse, WordScan


class ShortSentenceScorer:
    """The whole post scores low; any short text containing 'earn' scores `risk`."""
    version = "s-1"

    def __init__(self, risk=0.95):
        self.risk = risk
        self.calls = []

    def score(self, text):
        self.calls.append(text)
        r = self.risk if ("earn" in text.lower() and len(text) < 45) else 0.01
        return ScoreResult(r, "x", {"x": r}, self.version, 1.0)


def post(client, content):
    return client.post("/v1/moderate", json={"content": content, "content_type": "post"}).get_json()


TEXT = "Handmade leather wallets and belts. Earn 50000 per week from home."


def test_reject_level_sentence_rejects_whole_post(make_settings):
    body = post(create_app(make_settings(), scorer=ShortSentenceScorer()).test_client(), TEXT)
    assert body["decision"] == "reject" and body["decided_by"] == "sentence"
    assert body["risk_score"] == 0.01                      # whole text alone would be allowed
    worst = max(body["sentence_scores"], key=lambda x: x["risk_score"])
    assert TEXT[worst["start"]:worst["end"]] == "Earn 50000 per week from home"
    # The author is shown the sentence to rewrite, and the word that drives its risk.
    [issue] = body["feedback"]["issues"]
    assert TEXT[issue["start"]:issue["end"]] == "Earn 50000 per week from home"
    assert [TEXT[s:e] for s, e in issue["words"]] == ["Earn"]


def test_word_scores_measure_each_words_contribution(make_settings):
    body = post(create_app(make_settings(), scorer=ShortSentenceScorer()).test_client(), TEXT)
    scores = {TEXT[w["start"]:w["end"]]: w["contribution"] for w in body["word_scores"]}
    assert scores["Earn"] == pytest.approx(0.94)           # 0.95 with it, 0.01 without
    assert scores["week"] == 0.0 and scores["home"] == 0.0


def test_word_scan_can_be_disabled(make_settings):
    s = make_settings(word_scan=WordScan(enabled=False))
    body = post(create_app(s, scorer=ShortSentenceScorer()).test_client(), TEXT)
    assert body["decision"] == "reject" and body["word_scores"] == []
    assert "words" not in body["feedback"]["issues"][0]


def test_every_reject_level_sentence_is_highlighted(make_settings):
    text = "Earn 50000 per week. Handmade leather wallets. Earn big from home."
    body = post(create_app(make_settings(), scorer=ShortSentenceScorer()).test_client(), text)
    assert [text[i["start"]:i["end"]] for i in body["feedback"]["issues"]] == ["Earn 50000 per week", "Earn big from home"]


class PhraseScorer(ShortSentenceScorer):
    """Risky only when 'earn' appears in a short span (<= 60 chars): a long run-on dilutes it."""
    def score(self, text):
        self.calls.append(text)
        r = 0.95 if ("earn" in text.lower() and len(text) <= 60) else 0.01
        return ScoreResult(r, "x", {"x": r}, self.version, 1.0)


def test_word_windows_catch_a_phrase_in_a_long_run_on_sentence(make_settings):
    filler = " ".join(["quality cotton towels and bedsheets"] * 12)          # 60 words, no full stop
    text = f"{filler} earn 50000 weekly {filler}"
    s = make_settings(max_chars=5000, word_scan=WordScan(window_words=8, window_stride=4))
    body = post(create_app(s, scorer=PhraseScorer()).test_client(), text)
    assert body["decision"] == "reject" and body["decided_by"] == "sentence"
    windows = [x for x in body["sentence_scores"] if x.get("kind") == "window"]
    assert windows and any("earn" in text[w["start"]:w["end"]] for w in windows if w["risk_score"] > 0.5)


def test_short_sentences_get_no_windows(make_settings):
    body = post(create_app(make_settings(), scorer=ShortSentenceScorer(risk=0.01)).test_client(), TEXT)
    assert all("kind" not in x for x in body["sentence_scores"])


class BatchScorer(ShortSentenceScorer):
    batch_invariant = True

    def __init__(self):
        super().__init__()
        self.batches = []

    def score_batch(self, texts):
        self.batches.append(list(texts))
        return [ShortSentenceScorer.score(self, t) for t in texts]


def test_sentences_are_scored_in_one_batch_when_the_model_allows(make_settings):
    scorer = BatchScorer()
    text = "Leather wallets in stock. Belts in all sizes. Bags made to order. Shoes for every season."
    post(create_app(make_settings(targeted=TargetedAbuse(enabled=False)), scorer=scorer).test_client(), text)
    assert scorer.batches == [["Leather wallets in stock", "Belts in all sizes", "Bags made to order",
                               "Shoes for every season"]]


def test_short_fragments_are_merged_not_scored_alone():
    from app.targeted import scan_spans
    text = "Thanks. We ship cotton towels across India. Samples are free. Regards, Meena"
    # "Thanks" joins the next sentence; "Regards, Meena" (at the end) joins the previous one.
    assert [text[s:e] for s, e in scan_spans(text)] == ["Thanks. We ship cotton towels across India",
                                                         "Samples are free. Regards, Meena"]


@pytest.mark.parametrize("text,expected", [
    ("F1rst c0py R0lex w4tches ava1lable.", "First copy Rolex watches available."),
    ("S3nd y0ur 0TP n0w", "Send your OTP now"),
    ("D0ubl3 y0ur inv3stm3nt!", "Double your investment!"),
    # left alone: product codes, quantities, prices, punctuation
    ("Solar panels 330W, A16 chip, i5 CPU, 5kg bags, 3pm pickup, 1st floor, Rs 50,000!",
     "Solar panels 330W, A16 chip, i5 CPU, 5kg bags, 3pm pickup, 1st floor, Rs 50,000!"),
])
def test_deobfuscate_restores_leetspeak_only(text, expected):
    from app.targeted import deobfuscate
    assert deobfuscate(text) == expected and len(deobfuscate(text)) == len(text)


def test_leetspeak_is_scored_as_plain_text(make_settings):
    """The model only knows 'first copy'; the leetspeak reading is scored too and the riskier counts."""
    class CopyScorer(ShortSentenceScorer):
        def score(self, text):
            self.calls.append(text)
            r = 0.95 if "first copy" in text.lower() else 0.01
            return ScoreResult(r, "x", {"x": r}, self.version, 1.0)
    text = "Handmade leather wallets and belts. F1rst c0py R0lex w4tches ava1lable."
    body = post(create_app(make_settings(), scorer=CopyScorer()).test_client(), text)
    assert body["decision"] == "reject" and body["decided_by"] == "model"
    [issue] = body["feedback"]["issues"]
    assert text[issue["start"]:issue["end"]] == "F1rst c0py R0lex w4tches ava1lable"
    assert [text[s:e] for s, e in issue["words"]] == ["F1rst", "c0py"]


def test_whole_text_reject_highlights_the_right_sentence_even_late_in_a_long_post(make_settings):
    """The whole post is over the boundary; the harmful sentence is the 8th, past the first five."""
    class Whole(ShortSentenceScorer):
        def score(self, text):
            self.calls.append(text)
            r = 0.95 if "earn" in text.lower() and (len(text) < 45 or len(text) > 200) else 0.01
            return ScoreResult(r, "x", {"x": r}, self.version, 1.0)
    clean = [f"Our mill runs shift number {i} every day" for i in range(7)]
    text = ". ".join(clean + ["Earn 50000 per week from home"] + clean) + "."
    body = post(create_app(make_settings(max_chars=5000), scorer=Whole()).test_client(), text)
    assert body["decision"] == "reject" and body["decided_by"] == "model"
    assert [text[i["start"]:i["end"]] for i in body["feedback"]["issues"]] == ["Earn 50000 per week from home"]


def test_trigger_words_in_a_long_span_are_found_in_its_riskiest_window(make_settings):
    filler = " ".join(["quality cotton towels and bedsheets"] * 12)          # 60 words, no full stop
    text = f"{filler} earn 50000 weekly {filler}"
    scorer = PhraseScorer()
    s = make_settings(max_chars=5000, word_scan=WordScan(window_words=8, window_stride=4))
    body = post(create_app(s, scorer=scorer).test_client(), text)
    words = [text[s:e] for i in body["feedback"]["issues"] for s, e in i["words"]]
    assert words == ["earn"]
    assert max(len(t) for t in scorer.calls if "earn" in t and t != text) < 200   # variants are window-sized


def test_redundant_harm_trigger_words_found_greedily(make_settings):
    """No single word matters (0.99999 -> 0.9997 when one keyword is removed): the greedy search
    removes keywords one by one until the sentence is back under the boundary."""
    import math

    class Redundant(ShortSentenceScorer):
        KEYS = ("registration", "fee", "earn")

        def score(self, text):
            self.calls.append(text)
            c = sum(k in text.lower() for k in self.KEYS)
            r = 1 / (1 + math.exp(-(-2 + 5 * c)))
            return ScoreResult(r, "x", {"x": r}, self.version, 1.0)

    text = "Handmade leather wallets and belts. Dealers pay a registration fee and earn big."
    body = post(create_app(make_settings(), scorer=Redundant()).test_client(), text)
    assert body["decision"] == "reject"
    [issue] = body["feedback"]["issues"]
    assert text[issue["start"]:issue["end"]] == "Dealers pay a registration fee and earn big"
    assert [text[s:e] for s, e in issue["words"]] == ["registration", "fee", "earn"]
    assert all(w["contribution"] < 0.1 for w in body["word_scores"])      # single removals don't explain it


class KeywordScorer:
    """Flags any text containing the keyword, whatever its length."""
    version = "k-1"

    def __init__(self, key="earn"):
        self.key = key
        self.calls = []

    def score(self, text):
        self.calls.append(text)
        r = 0.95 if self.key in text.lower() else 0.01
        return ScoreResult(r, "x", {"x": r}, self.version, 1.0)


def test_word_scan_respects_its_call_budget(make_settings):
    """The word phase only draws highlights, so it is capped: one long flagged sentence must not
    turn into a hundred inferences, even with no pre-filter to shortlist it."""
    scorer = KeywordScorer()
    long_sentence = "Earn " + " ".join(f"word{i}" for i in range(80))
    text = f"Handmade leather wallets and belts in all sizes. {long_sentence}."
    s = make_settings(max_chars=5000, word_scan=WordScan(max_calls=12, max_candidates=6,
                                                         window_words=200, window_stride=100))
    body = post(create_app(s, scorer=scorer).test_client(), text)
    assert body["decision"] == "reject"
    assert len(body["word_scores"]) <= 12, len(body["word_scores"])
    assert len(scorer.calls) < 40, len(scorer.calls)


def test_shortlist_keeps_the_trigger_word_and_cuts_the_calls(make_settings):
    """With the pre-filter loaded, only the words it ranks highest reach the model — but the
    word that actually matters must survive the shortlist."""
    from tests.test_triage import make_triage
    scorer = KeywordScorer()
    text = ("Cotton towels and bedsheets for hotels in every size and colour. "
            "Earn big money quickly from your own home today.")
    s = make_settings(max_chars=5000, triage=make_triage(["earn"]),
                      word_scan=WordScan(max_candidates=4))
    body = post(create_app(s, scorer=scorer).test_client(), text)
    words = [text[w["start"]:w["end"]] for w in body["word_scores"]]
    assert "Earn" in words and len(words) <= 4, words


def test_shortlist_is_a_no_op_without_the_prefilter(make_settings):
    """No filter loaded: every word is measured, exactly as before."""
    scorer = ShortSentenceScorer()
    text = "Handmade leather wallets. Earn 50000 per week from home."
    body = post(create_app(make_settings(), scorer=scorer).test_client(), text)
    measured = [text[w["start"]:w["end"]] for w in body["word_scores"]]
    assert measured == ["Earn", "50000", "per", "week", "from", "home"]


def test_urls_and_file_names_are_not_split():
    from app.targeted import sentence_spans
    text = "See example.com/catalogue.pdf for 2.5 kg packs. Call us today"
    assert [text[s:e] for s, e in sentence_spans(text)] == ["See example.com/catalogue.pdf for 2.5 kg packs",
                                                             "Call us today"]


def test_middle_band_sentence_asks_for_revision(make_settings):
    body = post(create_app(make_settings(), scorer=ShortSentenceScorer(risk=0.5)).test_client(), TEXT)
    assert body["decision"] == "revise" and body["decided_by"] == "sentence"
    issues = body["feedback"]["issues"]
    assert [TEXT[i["start"]:i["end"]] for i in issues] == ["Earn 50000 per week from home"]


def test_middle_band_revise_can_be_turned_off(make_settings):
    s = make_settings(sentence_scan=SentenceScan(revise_on_middle=False))
    body = post(create_app(s, scorer=ShortSentenceScorer(risk=0.5)).test_client(), TEXT)
    assert body["decision"] == "allow" and len(body["sentence_scores"]) == 2


def test_single_sentence_post_is_not_rescanned(make_settings):
    scorer = ShortSentenceScorer()
    body = post(create_app(make_settings(), scorer=scorer).test_client(), "Handmade leather wallets")
    assert body["sentence_scores"] == [] and len(scorer.calls) == 1


def test_scan_is_capped(make_settings):
    scorer = ShortSentenceScorer()
    text = ". ".join(f"Line {i} is fine" for i in range(10))
    s = make_settings(sentence_scan=SentenceScan(max_sentences=3), targeted=TargetedAbuse(enabled=False))
    body = post(create_app(s, scorer=scorer).test_client(), text)
    assert len(body["sentence_scores"]) == 3 and len(scorer.calls) == 1 + 3


def test_scan_can_be_disabled(make_settings):
    s = make_settings(sentence_scan=SentenceScan(enabled=False))
    body = post(create_app(s, scorer=ShortSentenceScorer()).test_client(), TEXT)
    assert body["decision"] == "allow" and body["sentence_scores"] == []


def test_skipped_when_already_rejected(make_settings, tmp_path):
    class High(ShortSentenceScorer):
        def score(self, text):
            self.calls.append(text)
            return ScoreResult(0.9, "x", {"x": 0.9}, self.version, 1.0)
    msgs = tmp_path / "fb.yaml"
    msgs.write_text("highlight_on_reject: false\n")        # highlights would score sentences
    scorer = High()
    s = make_settings(feedback_messages_file=msgs, word_scan=WordScan(enabled=False))
    body = post(create_app(s, scorer=scorer).test_client(), TEXT)
    assert body["decided_by"] == "model" and body["sentence_scores"] == [] and len(scorer.calls) == 1


def test_sentences_are_scored_once(make_settings):
    """Scan + highlight + targeted checks share one cache per request."""
    scorer = ShortSentenceScorer(risk=0.5)   # middle band -> revise path also highlights sentences
    text = "Good wallets. They earn praise. Ships fast."
    post(create_app(make_settings(), scorer=scorer).test_client(), text)
    assert len(scorer.calls) == len(set(scorer.calls))


def test_invalid_max_sentences():
    with pytest.raises(ValueError):
        SentenceScan(max_sentences=0)




def test_harmful_sentence_deep_in_long_article_is_caught(make_settings):
    filler = " ".join(f"Paragraph {i} describes our cotton mill and its quality checks." for i in range(40))
    text = filler + " Earn 50000 per week from home. " + filler
    s = make_settings(max_chars=50_000)
    body = post(create_app(s, scorer=ShortSentenceScorer()).test_client(), text)
    assert body["decision"] == "reject" and body["decided_by"] == "sentence"
    assert len(body["sentence_scores"]) == 81          # every sentence, not just the first few
