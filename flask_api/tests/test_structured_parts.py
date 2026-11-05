"""Sending an article's parts, instead of joining them by hand.

`/v1/moderate` takes one `content` string, and every caller was expected to build it: title,
then body, then the hashtags expanded to words, separated just so. The reference client does
it correctly; a caller that did not had no way to find out, because a `title` sent beside
`content` was accepted and dropped on the floor. The response said approved, and the title
had never been looked at.

So the endpoint takes the parts now and does the joining itself.
"""
import pytest

from app import create_app
from tests.conftest import FixedScorer


def client(make_settings, risk=0.01, **kw):
    return create_app(make_settings(max_chars=25_000, **kw),
                      scorer=FixedScorer(risk)).test_client()


def post(c, **body):
    return c.post("/v1/moderate", json={"content_type": "article", **body})


# ---- the hole this closes ---------------------------------------------------------------

def test_a_harmful_title_sent_as_a_part_is_actually_checked(make_settings):
    """The whole point. `badword` is a block rule in the test gate.

    Before the endpoint took parts, this exact request was a 200 with the title unread: the
    body was clean, the gate saw only the body, and nothing anywhere said the title had been
    ignored.
    """
    r = post(client(make_settings), title="badword in the title", body="Perfectly clean body.")
    data = r.get_json()["Data"]
    assert data["Action"] == "reject"
    assert "badword in the title" in data["content"]


def test_the_title_reaches_the_content_that_gets_scored(make_settings):
    r = post(client(make_settings), title="Why our mill switched", body="We cut water use.")
    content = r.get_json()["Data"]["content"]
    assert content.startswith("Why our mill switched")
    assert "We cut water use." in content


# ---- the join matches the reference client -----------------------------------------------

def test_parts_are_joined_the_way_the_browser_client_joins_them(make_settings):
    """One string, however it was assembled.

    `buildArticleContent` in moderation-client.js puts a blank line between fields and
    expands each hashtag into words. If the service joined differently, the same article
    would score two ways depending on which caller sent it.
    """
    r = post(client(make_settings), title="A title", body="A body.",
             hashtags=["#dripIrrigation", "#first_copy"])
    data = r.get_json()["Data"]
    assert data["content"] == "A title\n\nA body.\n\ndrip Irrigation first copy"


def test_hashtags_also_accept_the_comma_separated_string_the_dotnet_dtos_carry(make_settings):
    """`CreateArticleDto.Hashtags` is a single string, not an array."""
    r = post(client(make_settings), title="T", body="B", hashtags="#one,#twoThings")
    assert r.get_json()["Data"]["content"] == "T\n\nB\n\none two Things"


def test_pascal_case_is_accepted_because_that_is_what_the_dtos_are_called(make_settings):
    """A .NET caller forwarding its own DTO field names is the likeliest way in."""
    r = post(client(make_settings), Title="A title", Body="A body.", Hashtags="#tag")
    data = r.get_json()["Data"]
    assert data["content"] == "A title\n\nA body.\n\ntag"
    assert data["fields"]["title"] == [0, 7]


# ---- where each part landed ---------------------------------------------------------------

def test_the_response_says_where_each_part_went(make_settings):
    """So the caller never has to redo the arithmetic to map an issue back to a box."""
    r = post(client(make_settings), title="A title", body="A body.", hashtags=["#tag"])
    data = r.get_json()["Data"]
    content, fields = data["content"], data["fields"]
    for name, (start, end) in fields.items():
        assert content[start:end], f"{name} range is empty"
    assert content[slice(*fields["title"])] == "A title"
    assert content[slice(*fields["body"])] == "A body."
    assert content[slice(*fields["hashtags"])] == "tag"


def test_an_empty_part_is_left_out_rather_than_padded(make_settings):
    """No blank line standing in for a field nobody filled in."""
    r = post(client(make_settings), title="Only a title", body="", hashtags=[])
    data = r.get_json()["Data"]
    assert data["content"] == "Only a title"
    assert set(data["fields"]) == {"title"}


def test_fields_is_absent_when_the_caller_did_its_own_joining(make_settings):
    """Nothing to report: the caller built the string and knows its own offsets."""
    r = post(client(make_settings), content="Already joined by the caller.")
    assert "fields" not in r.get_json()["Data"]


# ---- the two ways to get it wrong ----------------------------------------------------------

def test_content_and_parts_together_is_refused(make_settings):
    """Silently picking one is the class of bug this endpoint was built to stop."""
    r = post(client(make_settings), content="One string", title="And a part")
    assert r.status_code == 400
    data = r.get_json()["Data"]
    assert data["code"] == "invalid_request"
    assert data["parts"] == ["title"]


@pytest.mark.parametrize("parts", [
    {"title": "", "body": ""},
    {"title": "   ", "body": "\n"},
    {"hashtags": []},
])
def test_parts_that_are_all_empty_are_refused_not_approved(make_settings, parts):
    r = post(client(make_settings), **parts)
    assert r.status_code == 400
    assert r.get_json()["Data"]["code"] == "invalid_content"
    assert r.get_json()["Success"] is False


# ---- attachments the text endpoint cannot look at -----------------------------------------

DTO = {
    "UserId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "Title": "Why our mill switched to drip irrigation",
    "Body": "We cut water use by 40% over two seasons.",
    "ArticleType": "Case Study", "Visibility": "Public", "Status": "Draft",
    "ThumbnailUrl": "https://cdn.example.com/t.jpg",
    "Hashtags": "#dripIrrigation,#sustainableFarming",
    "PublishedBy": "Rudra Pratap Singh",
    "Medias": [{"MediaUrl": "https://cdn.example.com/a.jpg", "MediaType": "image",
                "Filename": "a.jpg", "SortOrder": 0}],
    "content_type": "article", "content_id": "ART-8831",
}


def test_a_forwarded_dotnet_dto_has_its_text_moderated(make_settings):
    """`CreateArticleDto` sent as-is: the three text fields are picked up and judged."""
    r = client(make_settings).post("/v1/moderate", json=DTO)
    data = r.get_json()["Data"]
    assert r.status_code == 200
    assert data["content"].startswith("Why our mill switched to drip irrigation")
    assert "We cut water use by 40%" in data["content"]
    assert "drip Irrigation sustainable Farming" in data["content"]
    assert sorted(data["fields"]) == ["body", "hashtags", "title"]


def test_attachments_on_a_text_call_are_counted_not_dropped(make_settings):
    """The DTO carries pictures. This endpoint reads text and cannot look at one of them.

    Answering a flat "approved" to an article with an unexamined image is the claim the rest
    of this service is built to avoid, so the count is reported and the message says it. The
    pictures themselves go to /v1/moderate/media, one request each.
    """
    body = client(make_settings).post("/v1/moderate", json=DTO).get_json()
    assert body["Data"]["attachments_not_checked"] == 2        # Medias[0] + ThumbnailUrl
    assert "not checked" in body["Message"]
    assert "approved for publication." != body["Message"]


def test_nothing_is_claimed_when_there_are_no_attachments(make_settings):
    """The honest note must not become noise on every request."""
    plain = {k: v for k, v in DTO.items() if k not in ("Medias", "ThumbnailUrl")}
    body = client(make_settings).post("/v1/moderate", json=plain).get_json()
    assert "attachments_not_checked" not in body["Data"]
    assert body["Message"] == "Content approved for publication."


def test_an_empty_medias_list_is_not_an_attachment(make_settings):
    plain = {**DTO, "Medias": [], "ThumbnailUrl": ""}
    body = client(make_settings).post("/v1/moderate", json=plain).get_json()
    assert "attachments_not_checked" not in body["Data"]
