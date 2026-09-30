"""An issue span must never cover two of the form's fields.

A form's inputs are joined into one `content` with a blank line between them
("<title>\\n\\n<body>", moderation-client.js buildContent), and the client maps each
feedback issue back onto whichever field its offsets fall in. A span that crosses the
separator therefore lands on both, and the author is told a clean business name is a problem.

The splitter already ended a sentence at a newline. What crossed the boundary was the step
after it: `scan_spans` merges a fragment of fewer than three words into the next sentence, so
a short title was absorbed into the first body sentence. QA 2026-09-30 caught it on the input
below, which returned one issue spanning offsets 0-84 (the whole thing) in 5 of 9 rejections
on Add Business.
"""
import pytest

from app import create_app
from app.model import ScoreResult
from app.targeted import scan_spans

TITLE = "Bhavani Textiles"
BODY = "Buy from us or we will burn your shop down, we know where you live."
CONTENT = f"{TITLE}\n\n{BODY}"


class BurnScorer:
    """Only text that talks about burning is risky; a business name is not."""
    version = "burn-1"

    def score(self, text):
        r = 0.99 if "burn" in text.lower() else 0.01
        return ScoreResult(r, "unsafe" if r > 0.5 else "safe", {"safe": 1 - r, "unsafe": r}, self.version, 1.0)


@pytest.fixture
def body(make_settings):
    client = create_app(make_settings(), scorer=BurnScorer()).test_client()
    return client.post("/v1/moderate",
                       json={"content": CONTENT, "content_type": "business_profile"}).get_json()


def test_the_body_sentence_is_rejected(body):
    assert body["decision"] == "reject"


def test_no_issue_overlaps_the_clean_title(body):
    """The regression check from the QA report: no issue may reach into offsets 0-15."""
    title = range(0, len(TITLE))
    for issue in body["feedback"]["issues"]:
        overlap = set(range(issue["start"], issue["end"])) & set(title)
        assert not overlap, f"issue {issue['start']}-{issue['end']} covers the title"


def test_the_issue_is_exactly_the_body_sentence(body):
    [issue] = body["feedback"]["issues"]
    assert CONTENT[issue["start"]:issue["end"]] == BODY.rstrip(".")


@pytest.mark.parametrize("content,expected", [
    # A short title is its own span, not part of the body.
    (CONTENT, [TITLE, BODY.rstrip(".")]),
    # Within one line, a short fragment still merges into its neighbour - that is what stops a
    # lone word being scored with no context around it.
    ("Thanks. We ship cotton fabric across Gujarat every week.",
     ["Thanks. We ship cotton fabric across Gujarat every week"]),
    ("We ship cotton fabric across Gujarat every week. Thanks.",
     ["We ship cotton fabric across Gujarat every week. Thanks"]),
    # Three fields, three spans.
    ("Pricing\n\nWholesale rates start at Rs 95 per kg.\n\nContact us for a quote.",
     ["Pricing", "Wholesale rates start at Rs 95 per kg", "Contact us for a quote"]),
    # A one-word line is still scanned rather than dropped.
    ("Hi", ["Hi"]),
])
def test_scan_spans_never_merge_across_a_line(content, expected):
    assert [content[s:e] for s, e in scan_spans(content)] == expected


def test_every_span_stays_within_one_field():
    content = "Sharma Traders\n\nWe supply brass fittings.\n\nhttps://example.test/catalogue"
    for s, e in scan_spans(content):
        assert "\n" not in content[s:e]
