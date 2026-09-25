from app import create_app
from app.model import ModelRegistry
from tests.conftest import FixedScorer

REQUIRED_KEYS = {
    "schema_version", "request_id", "status", "content_id", "content_type", "content",
    "content_sha256", "decision", "decided_by", "risk_score", "predicted_label",
    "label_scores", "gate_matches", "gate_version", "model_version", "thresholds",
    "latency_ms", "decided_at",
}


def post(client, content="Industrial valves for sale", content_type="product_listing", **extra):
    return client.post("/v1/moderate", json={"content": content, "content_type": content_type, **extra})


def test_allow(client, sink):
    r = post(client, content_id=42)
    body = r.get_json()
    assert r.status_code == 200
    assert body["decision"] == "allow" and body["decided_by"] == "model"
    assert body["content_id"] == "42"
    assert set(body) == REQUIRED_KEYS
    assert sink.results == [body]


def test_review_and_reject_by_score(make_settings, sink):
    for risk, expected in [(0.5, "review"), (0.9, "reject")]:
        c = create_app(make_settings(), scorer=FixedScorer(risk), sink=sink).test_client()
        assert post(c).get_json()["decision"] == expected


def test_gate_block_skips_model(client, scorer, sink):
    body = post(client, content="buy now badword").get_json()
    assert body["decision"] == "reject" and body["decided_by"] == "gate"
    assert body["risk_score"] is None and body["model_version"] is None
    assert body["gate_matches"][0]["rule_id"] == "test.block_term"
    assert scorer.calls == 0
    assert len(sink.results) == 1


def test_gate_flag_forces_review(client):
    body = post(client, content="details at bit.ly/xyz").get_json()
    assert body["decision"] == "review" and body["decided_by"] == "model"
    assert body["gate_matches"][0]["action"] == "flag"


def test_invalid_json(client):
    r = client.post("/v1/moderate", data="nope", content_type="application/json")
    assert r.status_code == 400 and r.get_json()["error"]["code"] == "invalid_json"


def test_empty_content(client):
    assert post(client, content="   ").status_code == 400


def test_non_string_content(client):
    assert post(client, content=123).status_code == 400


def test_unknown_content_type(client):
    r = post(client, content_type="tweet")
    assert r.status_code == 400
    assert "business_profile" in r.get_json()["error"]["allowed"]


def test_content_too_long(client):
    assert post(client, content="a" * 501).status_code == 413


def test_model_not_ready_returns_503(make_settings, monkeypatch):
    def boom(self, *a, **k):
        self.last_error = "FileNotFoundError: model.onnx missing"
        raise FileNotFoundError("model.onnx missing")
    monkeypatch.setattr(ModelRegistry, "load", boom)
    c = create_app(make_settings(model_backend="onnx")).test_client()
    r = post(c)
    assert r.status_code == 503 and r.get_json()["error"]["code"] == "model_not_ready"
    assert c.get("/ready").status_code == 503
    # gate still works without a model
    assert post(c, content="badword").get_json()["decision"] == "reject"


def test_inference_error_returns_500(make_settings):
    class Broken:
        version = "x"
        def score(self, text):
            raise RuntimeError("onnx exploded")
    c = create_app(make_settings(), scorer=Broken()).test_client()
    r = post(c)
    assert r.status_code == 500 and "request_id" in r.get_json()["error"]


def test_health_and_ready(client):
    assert client.get("/health").status_code == 200
    body = client.get("/ready").get_json()
    assert body["status"] == "ready" and body["model_version"] == "fixed-1"


def test_async_mode_returns_pending(make_settings, sink):
    queued = []
    c = create_app(make_settings(mode="async"), sink=sink, enqueue=queued.append,
                   fetch_result=lambda rid: {"request_id": rid, "status": "pending"}).test_client()
    r = post(c)
    body = r.get_json()
    assert r.status_code == 202 and body["status"] == "pending"
    assert queued[0].request_id == body["request_id"]
    assert sink.results == []  # worker emits, not Flask
    assert c.get(body["status_url"]).get_json()["status"] == "pending"


def test_async_mode_gate_block_is_immediate(make_settings, sink):
    queued = []
    c = create_app(make_settings(mode="async"), sink=sink, enqueue=queued.append).test_client()
    body = post(c, content="badword").get_json()
    assert body["status"] == "completed" and body["decision"] == "reject"
    assert queued == [] and len(sink.results) == 1


def test_async_enqueue_failure_503(make_settings):
    def fail(req):
        raise ConnectionError("redis down")
    c = create_app(make_settings(mode="async"), enqueue=fail).test_client()
    assert post(c).status_code == 503


def test_status_lookup_404_in_sync_mode(client):
    assert client.get("/v1/moderate/abc").status_code == 404


def test_admin_reload_requires_token(client):
    assert client.post("/v1/admin/model/reload").status_code == 401
    assert client.post("/v1/admin/model/reload", headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_admin_reload_swaps_model(client):
    r = client.post("/v1/admin/model/reload", headers={"X-Admin-Token": "secret"})
    body = r.get_json()
    assert r.status_code == 200
    assert body["previous_version"] == "fixed-1" and body["model_version"] == "stub-0"


def test_admin_disabled_without_token(make_settings, scorer):
    c = create_app(make_settings(admin_token=None), scorer=scorer).test_client()
    assert c.post("/v1/admin/model/reload", headers={"X-Admin-Token": ""}).status_code == 404
