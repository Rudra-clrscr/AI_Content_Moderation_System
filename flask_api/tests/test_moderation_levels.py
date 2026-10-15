"""Per-feature moderation levels, and the toggle that switches a feature off.

The toggle is the dangerous control in this codebase: it is the one setting whose job is to
stop content being checked. These tests pin what "off" means, that it cannot be reached by
accident, and - the thing that actually went wrong while building it - that a feature nobody
has configured keeps using the thresholds from settings.yaml rather than one this file made up.
"""
import pytest

from app import create_app
from app.levels import ModerationLevels
from app.routing import Thresholds
from tests.conftest import FixedScorer

LEVELS_YAML = """
version: 1
levels:
  strict:  {allow_max: 0.25, reject_min: 0.25}
  medium:  {allow_max: 0.50, reject_min: 0.50}
  lenient: {allow_max: 0.85, reject_min: 0.85}
default:
  enabled: true
features:
  article:          {enabled: true, level: strict}
  proposal:         {enabled: true, level: lenient}
  post:             {enabled: false}
  product_listing:  {enabled: true, reject_min: 0.62}
"""


@pytest.fixture
def levels_file(tmp_path):
    path = tmp_path / "moderation_levels.yaml"
    path.write_text(LEVELS_YAML, encoding="utf-8")
    return path


def client_with(make_settings, sink, levels_file, risk=0.5):
    app = create_app(make_settings(moderation_levels_file=levels_file),
                     scorer=FixedScorer(risk), sink=sink)
    return app.test_client()


def post(client, content="Industrial valves for sale", content_type="product_listing"):
    return client.post("/v1/moderate",
                       json={"content": content, "content_type": content_type})


# ---- the default: configure nothing, change nothing ---------------------------------

def test_an_unconfigured_feature_uses_the_thresholds_from_settings(make_settings, sink, levels_file):
    """The regression that twelve tests caught while this was being written.

    `video` is absent from the file above, so it must be judged at settings.yaml's
    Thresholds(0.3, 0.7) - which has a revise band. An earlier version defaulted every
    unlisted feature to `medium` (0.5/0.5), silently removing that band and overriding an
    operator's own configuration with one this feature invented.
    """
    c = client_with(make_settings, sink, levels_file, risk=0.5)
    body = post(c, content_type="video").get_json()
    assert body["decision"] == "revise"                      # 0.3 < 0.5 < 0.7
    assert body["thresholds"] == {"allow_max": 0.3, "reject_min": 0.7}
    assert body["moderation"] == {"enabled": True}           # no level: deferring


def test_the_shipped_config_configures_no_feature(make_settings, sink):
    """What ships must change nothing until an admin chooses. A file that silently retunes
    every surface on upgrade is not a feature, it is an incident."""
    app = create_app(make_settings(), scorer=FixedScorer(0.5), sink=sink)
    levels = app.extensions["moderation"].levels
    assert levels.as_dict()["features"] == {}
    assert levels.disabled_features() == []
    assert levels.for_type("article").thresholds is None     # defers to settings.thresholds


# ---- the rating, 0 to 1 --------------------------------------------------------------

def test_a_stricter_level_refuses_what_the_global_bar_allows(make_settings, sink, levels_file):
    c = client_with(make_settings, sink, levels_file, risk=0.4)
    assert post(c, content_type="video").get_json()["decision"] == "revise"    # global 0.3/0.7
    article = post(c, content_type="article").get_json()                       # strict 0.25
    assert article["decision"] == "reject"
    assert article["moderation"] == {"enabled": True, "level": "strict",
                                     "allow_max": 0.25, "reject_min": 0.25}


def test_a_looser_level_publishes_what_the_global_bar_would_not(make_settings, sink, levels_file):
    c = client_with(make_settings, sink, levels_file, risk=0.8)
    assert post(c, content_type="video").get_json()["decision"] == "reject"    # 0.8 > 0.7
    assert post(c, content_type="proposal").get_json()["decision"] == "allow"  # 0.8 <= 0.85


def test_a_raw_rating_is_honoured_without_a_named_level(make_settings, sink, levels_file):
    """The admin UI offers three buttons; an expert types a number. Both reach the same place."""
    c = client_with(make_settings, sink, levels_file, risk=0.6)
    body = post(c, content_type="product_listing").get_json()
    assert body["decision"] == "allow"                        # 0.60 <= 0.62
    assert body["moderation"] == {"enabled": True, "allow_max": 0.62, "reject_min": 0.62}
    assert "level" not in body["moderation"]                  # a number was given, not a name


# ---- the toggle ----------------------------------------------------------------------

def test_a_disabled_feature_publishes_without_checking_anything(make_settings, sink, levels_file):
    c = client_with(make_settings, sink, levels_file, risk=0.99)
    body = post(c, content="pay in crypto to double your money", content_type="post").get_json()
    assert body["decision"] == "allow"
    assert body["decided_by"] == "disabled"
    assert body["risk_score"] is None                 # nothing was scored
    assert body["gate_matches"] == []                 # the gate did not run either
    assert body["moderation"] == {"enabled": False}


def test_disabled_is_distinguishable_from_checked_and_clean(make_settings, sink, levels_file):
    """The whole point of `decided_by: disabled`. A consumer that cannot tell these apart will
    treat unchecked content as vetted, which is how a toggle becomes a breach."""
    c = client_with(make_settings, sink, levels_file, risk=0.01)
    off = post(c, content_type="post").get_json()
    on = post(c, content_type="video").get_json()
    assert off["decision"] == on["decision"] == "allow"
    assert (off["decided_by"], on["decided_by"]) == ("disabled", "model")
    assert off["risk_score"] is None and on["risk_score"] is not None


def test_the_audit_row_records_the_level_it_was_judged_at(make_settings, sink, levels_file):
    """The sink is what anyone reads months later to ask why something was published."""
    c = client_with(make_settings, sink, levels_file, risk=0.1)
    post(c, content_type="article")
    post(c, content_type="post")
    assert sink.results[0]["moderation"]["level"] == "strict"
    assert sink.results[1]["moderation"] == {"enabled": False}
    assert sink.results[1]["decided_by"] == "disabled"


def test_ready_reports_which_features_are_unmoderated(make_settings, sink, levels_file):
    """An operator should read this from outside, not infer it from what gets published."""
    c = client_with(make_settings, sink, levels_file)
    assert c.get("/ready").get_json()["moderation_disabled_for"] == ["post"]


# ---- the admin API -------------------------------------------------------------------

def test_admin_endpoints_need_the_token(make_settings, sink, levels_file):
    c = client_with(make_settings, sink, levels_file)
    assert c.get("/v1/admin/moderation-levels").status_code == 401
    assert c.put("/v1/admin/moderation-levels/post", json={"enabled": True}).status_code == 401
    ok = {"X-Admin-Token": "secret"}
    assert c.get("/v1/admin/moderation-levels", headers=ok).status_code == 200


def test_admin_endpoints_are_absent_when_no_token_is_configured(make_settings, sink, levels_file):
    app = create_app(make_settings(moderation_levels_file=levels_file, admin_token=None),
                     scorer=FixedScorer(), sink=sink)
    c = app.test_client()
    assert c.get("/v1/admin/moderation-levels").status_code == 404


def test_a_toggle_takes_effect_on_the_next_request(make_settings, sink, levels_file):
    c = client_with(make_settings, sink, levels_file, risk=0.99)
    ok = {"X-Admin-Token": "secret"}
    assert post(c, content_type="article").get_json()["decision"] == "reject"
    r = c.put("/v1/admin/moderation-levels/article", json={"enabled": False}, headers=ok)
    assert r.status_code == 200 and r.get_json()["enabled"] is False
    assert post(c, content_type="article").get_json()["decided_by"] == "disabled"
    r = c.put("/v1/admin/moderation-levels/article", json={"enabled": True}, headers=ok)
    assert r.status_code == 200
    assert post(c, content_type="article").get_json()["decision"] == "reject"


def test_switching_back_on_restores_the_level_it_had(make_settings, sink, levels_file):
    """Turning a feature off must not quietly forget it was `strict`."""
    c = client_with(make_settings, sink, levels_file, risk=0.4)
    ok = {"X-Admin-Token": "secret"}
    c.put("/v1/admin/moderation-levels/article", json={"enabled": False}, headers=ok)
    body = c.put("/v1/admin/moderation-levels/article", json={"enabled": True},
                 headers=ok).get_json()
    assert body["level"] == "strict" and body["reject_min"] == 0.25
    assert post(c, content_type="article").get_json()["decision"] == "reject"


def test_the_rating_can_be_set_live(make_settings, sink, levels_file):
    c = client_with(make_settings, sink, levels_file, risk=0.4)
    ok = {"X-Admin-Token": "secret"}
    assert post(c, content_type="article").get_json()["decision"] == "reject"   # strict 0.25
    c.put("/v1/admin/moderation-levels/article", json={"reject_min": 0.9}, headers=ok)
    assert post(c, content_type="article").get_json()["decision"] == "allow"


@pytest.mark.parametrize("body", [
    {"level": "nonexistent"},
    {"reject_min": 1.5},
    {"reject_min": -0.1},
    {"allow_max": 0.9, "reject_min": 0.2},     # allow_max must not exceed reject_min
    {"nonsense": True},
    {},
])
def test_invalid_changes_are_refused(make_settings, sink, levels_file, body):
    """A malformed admin call must not land halfway and must not loosen anything."""
    c = client_with(make_settings, sink, levels_file, risk=0.4)
    ok = {"X-Admin-Token": "secret"}
    assert c.put("/v1/admin/moderation-levels/article", json=body, headers=ok).status_code == 400
    assert post(c, content_type="article").get_json()["decision"] == "reject"   # still strict


# ---- loading ---------------------------------------------------------------------------

def test_a_missing_file_moderates_everything_rather_than_nothing(tmp_path):
    """A config problem must never be the reason content stops being checked."""
    levels = ModerationLevels.from_yaml(tmp_path / "absent.yaml")
    assert levels.for_type("article").enabled is True
    assert levels.disabled_features() == []


def test_a_malformed_file_moderates_everything_rather_than_nothing(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("features: [this is not a mapping\n", encoding="utf-8")
    levels = ModerationLevels.from_yaml(path)
    assert levels.for_type("post").enabled is True


def test_one_malformed_entry_does_not_disable_the_others(tmp_path):
    path = tmp_path / "partly.yaml"
    path.write_text("""
features:
  article: {enabled: true, level: no_such_level}
  post:    {enabled: true, level: strict}
""", encoding="utf-8")
    levels = ModerationLevels.from_yaml(path)
    assert "article" not in levels.as_dict()["features"]      # dropped, not guessed at
    assert levels.for_type("article").enabled is True         # and still moderated
    assert levels.for_type("post").thresholds == Thresholds(0.25, 0.25)
