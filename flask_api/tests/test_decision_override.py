"""An admin overturning a moderation decision (refused -> approved, or the other way).

The .NET side publishes; this service keeps the record. So an override must change what
every admin screen and count sees - the log's verdict, its filters, the repeat-offender rule -
while keeping what the model said, and answer in the platform's own words for .NET to act on.
"""
from __future__ import annotations

import pytest

ADMIN = {"X-Admin-Token": "secret"}


@pytest.fixture
def store(client):
    return client.application.extensions["admin"].store


def _log(store, request_id, decision="reject", *, user="member-1", content_id="art-1",
         content_type="article"):
    store.record_moderation_log({"request_id": request_id, "content_type": content_type,
                                 "content_id": content_id, "decision": decision,
                                 "user_id": user, "modality": "text", "content": "x"})


def _override(client, rid, body, headers=ADMIN):
    return client.post(f"/v1/admin/moderation-logs/{rid}/override", json=body, headers=headers)


def test_a_refusal_approved_by_an_admin(client, store):
    _log(store, "r1", "reject")
    r = _override(client, "r1", {"decision": "approved", "reason": "a quote, not abuse"})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["Action"] == "approved" and body["decision"] == "approved"
    assert body["overridden"] is True and body["model_decision"] == "reject"
    assert body["reason"] == "a quote, not abuse"
    assert body["content_id"] == "art-1" and body["content_type"] == "article"

    row = client.get("/v1/admin/moderation-logs/r1", headers=ADMIN).get_json()
    assert row["decision"] == "allow" and row["original_decision"] == "reject"
    assert row["override_reason"] == "a quote, not abuse" and row["overridden_at"]


def test_the_log_counts_and_filters_follow_the_admin(client, store):
    _log(store, "r1", "reject")
    _log(store, "r2", "reject")
    _override(client, "r1", {"decision": "approved"})
    d = client.get("/v1/admin/moderation-logs", headers=ADMIN).get_json()
    assert d["stats"]["reject"] == 1 and d["stats"]["allow"] == 1
    d = client.get("/v1/admin/moderation-logs?decision=allow", headers=ADMIN).get_json()
    assert [x["request_id"] for x in d["logs"]] == ["r1"]
    assert d["logs"][0]["original_decision"] == "reject"


def test_an_approved_refusal_no_longer_counts_against_the_member(client, store):
    for i in range(3):
        _log(store, f"r{i}", "reject", user="member-9")
    rec = lambda: client.get(  # noqa: E731
        "/v1/admin/moderation-logs/repeat-offenders/member-9", headers=ADMIN).get_json()
    assert rec()["is_repeat_offender"] is True
    _override(client, "r0", {"decision": "approved"})
    assert rec()["rejected"] == 2 and rec()["is_repeat_offender"] is False


def test_approved_content_can_be_refused_too(client, store):
    _log(store, "r1", "allow")
    body = _override(client, "r1", {"Action": "reject", "Note": "scam, missed"}).get_json()
    assert body["Action"] == "reject" and body["model_decision"] == "approved"
    assert body["reason"] == "scam, missed"


def test_a_second_override_keeps_what_the_model_said(client, store):
    _log(store, "r1", "reject")
    _override(client, "r1", {"decision": "approved"})
    _override(client, "r1", {"decision": "approved", "reason": "second look"})
    row = client.get("/v1/admin/moderation-logs/r1", headers=ADMIN).get_json()
    assert row["original_decision"] == "reject" and row["override_reason"] == "second look"


def test_overriding_back_to_the_models_verdict_is_an_undo(client, store):
    _log(store, "r1", "reject")
    _override(client, "r1", {"decision": "approved"})
    body = _override(client, "r1", {"decision": "reject"}).get_json()
    assert body["overridden"] is False and body["Action"] == "reject"
    row = client.get("/v1/admin/moderation-logs/r1", headers=ADMIN).get_json()
    assert row["decision"] == "reject" and row["original_decision"] is None


def test_delete_undoes_the_override(client, store):
    _log(store, "r1", "reject")
    _override(client, "r1", {"decision": "approved"})
    r = client.delete("/v1/admin/moderation-logs/r1/override", headers=ADMIN)
    assert r.status_code == 200 and r.get_json()["Action"] == "reject"
    assert r.get_json()["overridden"] is False
    # Undoing nothing is not an error: the answer is still the verdict that stands.
    assert client.delete("/v1/admin/moderation-logs/r1/override",
                         headers=ADMIN).get_json()["Action"] == "reject"


def test_by_the_platforms_own_ids_takes_the_newest_decision(client, store):
    _log(store, "old", "reject", content_id="art-7")
    _log(store, "new", "reject", content_id="art-7")
    r = client.post("/v1/admin/moderation-logs/override", headers=ADMIN,
                    json={"content_type": "article", "content_id": "art-7",
                          "decision": "approved"})
    assert r.status_code == 200 and r.get_json()["request_id"] == "new"
    assert client.get("/v1/admin/moderation-logs/old", headers=ADMIN).get_json()["decision"] \
        == "reject"


def test_bad_requests(client, store):
    _log(store, "r1", "reject")
    for body in ({}, {"decision": "maybe"}, {"decision": "revise"}):
        r = _override(client, "r1", body)
        assert r.status_code == 400 and r.get_json()["Data"]["code"] == "bad_decision"
    assert _override(client, "nope", {"decision": "approved"}).status_code == 404
    assert client.delete("/v1/admin/moderation-logs/nope/override",
                         headers=ADMIN).status_code == 404
    r = client.post("/v1/admin/moderation-logs/override", headers=ADMIN,
                    json={"decision": "approved"})
    assert r.status_code == 400 and r.get_json()["Data"]["code"] == "missing_content"
    r = client.post("/v1/admin/moderation-logs/override", headers=ADMIN,
                    json={"content_type": "article", "content_id": "none", "decision": "approved"})
    assert r.status_code == 404


def test_it_needs_an_admin(client, store):
    _log(store, "r1", "reject")
    assert _override(client, "r1", {"decision": "approved"}, headers={}).status_code == 401
    assert client.delete("/v1/admin/moderation-logs/r1/override").status_code == 401
    assert client.post("/v1/admin/moderation-logs/override",
                       json={"content_type": "article", "content_id": "art-1",
                             "decision": "approved"}).status_code == 401
    row = client.get("/v1/admin/moderation-logs/r1", headers=ADMIN).get_json()
    assert row["decision"] == "reject"


def test_the_export_carries_the_override(client, store):
    _log(store, "r1", "reject")
    _override(client, "r1", {"decision": "approved", "reason": "fine on review"})
    out = client.get("/v1/admin/moderation-logs/export?format=txt", headers=ADMIN).data.decode()
    assert "Override reason" in out and "fine on review" in out
