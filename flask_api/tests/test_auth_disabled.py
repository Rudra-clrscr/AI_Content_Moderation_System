"""AUTH_DISABLED=1: no bearer token on /v1/moderate*, no login on /v1/admin/*.

Staging runs this way while the .NET backend decides how to authenticate its own calls, so
the switch has to open both doors - and only when it is set.
"""
import yaml

from app import create_app


def _client(make_settings, **kw):
    return create_app(settings=make_settings(**kw)).test_client()


def test_off_by_default_admin_still_needs_a_login(make_settings):
    c = _client(make_settings, service_token="s3cret")
    assert c.post("/v1/moderate", json={"content": "hello", "content_type": "post"}).status_code == 401
    assert c.get("/v1/admin/moderation-levels").status_code in (401, 404)
    ready = c.get("/ready").get_json()["Data"]
    assert ready["service_auth"] == "enforced" and ready["admin_auth"] == "enforced"


def test_disabled_opens_moderation_even_with_a_token_set(make_settings):
    c = _client(make_settings, service_token="s3cret", auth_disabled=True)
    r = c.post("/v1/moderate", json={"content": "hello there", "content_type": "post"})
    assert r.status_code == 200, r.get_json()


def test_disabled_opens_admin_reads_and_writes_without_a_login(make_settings):
    c = _client(make_settings, auth_disabled=True)
    assert c.get("/v1/admin/moderation-levels").status_code == 200
    # A write, with no session and no CSRF header.
    r = c.put("/v1/admin/moderation-levels/job", json={"level": "lenient"})
    assert r.status_code == 200, r.get_json()
    ready = c.get("/ready").get_json()["Data"]
    assert ready["service_auth"] == "open" and ready["admin_auth"] == "open"


def test_disabled_serves_a_spec_with_no_security(make_settings):
    spec_on = yaml.safe_load(_client(make_settings).get("/openapi.yaml").data)
    assert "securitySchemes" in spec_on["components"]
    spec = yaml.safe_load(_client(make_settings, auth_disabled=True).get("/openapi.yaml").data)
    assert spec["security"] == []
    assert "securitySchemes" not in spec.get("components", {})
    for ops in spec["paths"].values():
        for op in ops.values():
            if isinstance(op, dict):
                assert "security" not in op
    assert "switched off" in spec["info"]["description"]
