"""The bearer token on the moderation endpoints.

This service runs on its own AI/ML host and the main .NET server calls it across the network,
so the submission endpoints are no longer protected by being unreachable. They take
`Authorization: Bearer <SERVICE_TOKEN>` - the company API's own scheme, so the calling side
reuses the handler it has.

The awkward part these tests pin down: with `SERVICE_TOKEN` unset the endpoints are OPEN,
because every local checkout and the rest of this suite depends on that. The protection is
real only once the token is set, which is why `/ready` reports which of the two a deployment
ended up with.
"""
import io

from app import create_app

TOKEN = "s3rvice-t0ken"
BEARER = {"Authorization": f"Bearer {TOKEN}"}


def client(make_settings, **kw):
    return create_app(make_settings(**kw)).test_client()


def post(c, headers=None):
    return c.post("/v1/moderate", json={"content": "Industrial valves for sale",
                                        "content_type": "product_listing"},
                  headers=headers or {})


def upload(c, headers=None):
    return c.post("/v1/moderate/media",
                  data={"file": (io.BytesIO(b"not really a picture"), "x.png")},
                  content_type="multipart/form-data", headers=headers or {})


# ---- with a token configured ----------------------------------------------------------

def test_a_submission_without_the_header_is_refused(make_settings):
    r = post(client(make_settings, service_token=TOKEN))
    assert r.status_code == 401
    body = r.get_json()
    assert body["Success"] is False and body["Data"]["code"] == "unauthorized"
    # Which half of the header was wrong is the one thing worth knowing to someone guessing,
    # so the message says neither.
    assert TOKEN not in body["Message"]


def test_the_right_token_gets_a_decision(make_settings):
    r = post(client(make_settings, service_token=TOKEN), BEARER)
    assert r.status_code == 200
    # That it reached the pipeline at all is the point. Which way the stub model scored this
    # sentence is not this test's business, so assert a verdict was reached, not which one.
    assert r.get_json()["Data"]["Action"] in ("approved", "reject")


def test_a_wrong_token_is_refused(make_settings):
    r = post(client(make_settings, service_token=TOKEN), {"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_the_scheme_must_be_bearer(make_settings):
    """A token in the right header under the wrong scheme is not a near miss to be forgiven."""
    c = client(make_settings, service_token=TOKEN)
    assert post(c, {"Authorization": TOKEN}).status_code == 401
    assert post(c, {"Authorization": f"Basic {TOKEN}"}).status_code == 401
    # The scheme itself is matched case-insensitively, as RFC 7235 requires.
    assert post(c, {"Authorization": f"bearer {TOKEN}"}).status_code == 200


def test_the_media_endpoint_is_gated_too(make_settings):
    """The upload path is a submission like any other - and the expensive one to leave open."""
    c = client(make_settings, service_token=TOKEN)
    assert upload(c).status_code == 401
    # With the token it gets as far as the media checks, which is all this test needs: a 401
    # would mean the gate ran instead.
    assert upload(c, BEARER).status_code != 401


def test_the_status_lookup_is_gated(make_settings):
    """It hands back a whole result to anyone who can guess a request id."""
    c = client(make_settings, service_token=TOKEN)
    assert c.get("/v1/moderate/f1e2d3c4").status_code == 401


def test_the_probes_stay_open(make_settings):
    """A load balancer has to reach these without being given a secret."""
    c = client(make_settings, service_token=TOKEN)
    assert c.get("/health").status_code == 200
    assert c.get("/ready").status_code == 200


def test_ready_says_the_token_is_enforced(make_settings):
    body = client(make_settings, service_token=TOKEN).get("/ready").get_json()
    assert body["Data"]["service_auth"] == "enforced"


# ---- with no token configured ---------------------------------------------------------

def test_without_a_token_the_endpoints_are_open(make_settings):
    """The documented default, pinned so nobody has to discover it from a deployment.

    Not an endorsement: `/ready` is made to say so in the test below precisely because this
    is the state a deploy that forgot `SERVICE_TOKEN` is silently left in.
    """
    assert post(client(make_settings)).status_code == 200


def test_ready_says_so_when_it_is_open(make_settings):
    """The whole reason this field exists: from outside, a deployment that forgot the token
    looks exactly like one that never wanted it. A deploy check asserts `enforced`."""
    body = client(make_settings).get("/ready").get_json()
    assert body["Data"]["service_auth"] == "open"


def test_a_bearer_header_is_ignored_when_no_token_is_set(make_settings):
    """Nothing to compare against, so nothing is rejected - a stray header is not a refusal."""
    assert post(client(make_settings), {"Authorization": "Bearer anything"}).status_code == 200
