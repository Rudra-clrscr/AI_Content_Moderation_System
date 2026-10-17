"""The public-surface grievance model, and the line it must not cross.

The failure these tests exist to catch is not "a complaint got published". It is "a complaint
got refused in a proposal", which would break the dispute traffic BONC exists to carry and
would do it quietly, because the author would simply be told to rewrite.

They run against a stub scorer rather than the shipped bundle: what is being pinned is the
WIRING - which surfaces are asked, what the result says, what happens when no bundle is
installed - and that must hold whatever the model's accuracy is on the day.
"""
import pytest

from app.grievance import GrievanceCheck

PUBLIC = ("article", "post", "advertisement")
PRIVATE = ("proposal", "request", "business_proposal", "product_listing", "business_profile")


class _AlwaysSure:
    """Scores everything 1.0, so any surface that consults it refuses everything. A blunt
    instrument on purpose: if a private surface ever reaches the model, these tests fail
    loudly instead of depending on what a real classifier happens to think."""

    class _R:
        risk_score = 1.0

    def score(self, text):
        return self._R()


@pytest.fixture
def check():
    return GrievanceCheck(enabled=True, threshold=0.5, surfaces=PUBLIC,
                          version="test", scorer=_AlwaysSure())


@pytest.mark.parametrize("surface", PUBLIC)
def test_a_public_surface_is_checked(check, surface):
    refused, score = check.check("nobody has replied about my refund in two weeks", surface)
    assert refused and score == 1.0


@pytest.mark.parametrize("surface", PRIVATE)
def test_a_private_surface_is_never_asked(check, surface):
    """Not "allowed" - NOT ASKED. `score is None` is the record that nothing looked, which is
    what lets an operator tell this apart from a text that was checked and passed."""
    refused, score = check.check("nobody has replied about my refund in two weeks", surface)
    assert not refused
    assert score is None


def test_an_unknown_surface_is_not_checked(check):
    """The opposite of gate.py's answer for the same situation, deliberately.

    A gate rule is narrow and auditable, so running it on a surface nobody named costs little.
    This refuses an entire genre, so running it where nobody chose it would refuse dispute
    traffic on a surface that was never reviewed. A new content_type gets the main model only,
    until somebody adds it to settings.yaml on purpose.
    """
    assert check.check("my refund never came", "some_new_surface") == (False, None)
    assert check.check("my refund never came", None) == (False, None)


def test_a_missing_bundle_disables_the_check_instead_of_failing_to_start(tmp_path, caplog):
    """The service must come up without the bundle - slower to catch complaints, not dead.
    The gate rule is still there, which is what the log line tells the operator."""
    off = GrievanceCheck.load(tmp_path / "not-here")
    assert not off.enabled
    assert off.check("my refund never came", "article") == (False, None)
    assert "gate rule" in caplog.text.lower()


def test_disabled_by_configuration_never_loads_anything(tmp_path):
    assert not GrievanceCheck.load(tmp_path, enabled=False).enabled


def test_the_threshold_is_a_boundary_not_a_suggestion():
    class _At:
        class _R:
            risk_score = 0.60
        def score(self, text):
            return self._R()

    at = GrievanceCheck(enabled=True, threshold=0.60, surfaces=PUBLIC, scorer=_At())
    assert at.check("x", "article")[0] is True          # >= threshold refuses
    just_over = GrievanceCheck(enabled=True, threshold=0.61, surfaces=PUBLIC, scorer=_At())
    assert just_over.check("x", "article")[0] is False


def test_pipeline_records_a_grievance_refusal_as_its_own_decision(monkeypatch):
    """`decided_by` must say `grievance`, not `model`.

    The main model scores this text as safe and would publish it in a proposal, on purpose.
    Recording the refusal as a model decision would make the two look inconsistent when they
    are not, and would send whoever debugs it to the wrong model.
    """
    from app import create_app
    app = create_app()
    client = app.test_client()
    svc = app.extensions["moderation"] if "moderation" in app.extensions else None
    pipeline = svc.pipeline if svc else None
    if pipeline is None:                                    # pragma: no cover
        pytest.skip("service not exposed on app.extensions in this build")
    monkeypatch.setattr(pipeline, "grievance",
                        GrievanceCheck(enabled=True, threshold=0.5, surfaces=PUBLIC,
                                       version="test", scorer=_AlwaysSure()))

    body = {"content": "Nobody has replied about my refund in two weeks.", "content_id": "g1"}
    article = client.post("/v1/moderate", json={**body, "content_type": "article"}).get_json()
    assert article["decision"] == "reject"
    assert article["decided_by"] == "grievance"
    assert article["risk_score"] == 1.0
    assert article["feedback"]["title"]

    proposal = client.post("/v1/moderate", json={**body, "content_type": "proposal"}).get_json()
    assert proposal["decision"] == "allow"
    assert proposal["decided_by"] != "grievance"
