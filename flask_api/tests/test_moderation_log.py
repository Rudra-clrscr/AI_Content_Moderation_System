"""The audit log behind the dashboard's Moderation Logs card.

The table, the store methods, the API endpoints and the UI all existed while nothing wrote a
single row: AdminStoreSink and ChainedSink were defined and never instantiated, so every
decision went to the primary sink alone and the card was permanently empty. These tests assert
the wiring itself, which is the part that was missing.
"""
import pytest

from app import create_app
from app.sinks import AdminStoreSink, ChainedSink, MemorySink


def _store(app):
    return app.extensions["admin"].store


def test_audit_sink_is_wired_into_the_chain(make_settings, scorer, sink):
    """create_app must chain the audit sink on, not leave it to be wired by hand."""
    app = create_app(make_settings(), scorer=scorer, sink=sink)
    svc = app.extensions["moderation"]
    assert isinstance(svc.sink, ChainedSink)
    kinds = [type(s).__name__ for s in svc.sink.sinks]
    assert "AdminStoreSink" in kinds
    # The injected sink must survive the wrapping, or every other test's assertions are hollow.
    assert sink in svc.sink.sinks


def test_a_decision_lands_in_the_audit_log(make_settings, scorer, sink):
    app = create_app(make_settings(), scorer=scorer, sink=sink)
    client = app.test_client()

    r = client.post("/v1/moderate", json={"content": "An ordinary sentence about nothing.",
                                          "content_type": "article", "content_id": "c-1"})
    assert r.status_code == 200
    request_id = r.get_json()["request_id"]

    page = _store(app).get_moderation_logs()
    assert page["total"] == 1
    row = page["logs"][0]
    assert row["request_id"] == request_id
    assert row["decision"] == r.get_json()["decision"]
    assert row["content_type"] == "article"
    assert row["content_preview"].startswith("An ordinary sentence")
    # The primary sink still saw it too.
    assert len(sink.results) == 1


def test_the_detail_endpoint_finds_the_row_the_list_returned(make_settings, scorer, sink):
    app = create_app(make_settings(), scorer=scorer, sink=sink)
    client = app.test_client()
    r = client.post("/v1/moderate", json={"content": "Another ordinary sentence.",
                                          "content_type": "article"})
    request_id = r.get_json()["request_id"]

    detail = _store(app).get_moderation_log_by_request_id(request_id)
    assert detail is not None
    assert detail["request_id"] == request_id
    assert detail["raw_result"], "the full result is what the audit modal shows"


def test_a_failing_audit_write_does_not_fail_the_decision(make_settings, scorer, sink):
    """The audit log is a record, not a gate: if it breaks, moderation still answers."""
    app = create_app(make_settings(), scorer=scorer, sink=sink)

    class Exploding:
        def record_moderation_log(self, result):
            raise RuntimeError("disk full")

    svc = app.extensions["moderation"]
    svc.sink = ChainedSink(sink, AdminStoreSink(Exploding()))

    r = app.test_client().post("/v1/moderate", json={"content": "Still has to answer.",
                                                     "content_type": "article"})
    assert r.status_code == 200
    assert len(sink.results) == 1


def test_clearing_empties_the_log(make_settings, scorer, sink):
    app = create_app(make_settings(), scorer=scorer, sink=sink)
    client = app.test_client()
    for i in range(3):
        client.post("/v1/moderate", json={"content": f"Sentence number {i}.",
                                          "content_type": "article"})
    store = _store(app)
    assert store.get_moderation_logs()["total"] == 3
    assert store.clear_moderation_logs() == 3
    assert store.get_moderation_logs()["total"] == 0


def test_the_user_column_does_not_pass_off_the_content_id_as_an_author(make_settings, scorer, sink):
    """A content id is not a user. Falling back to it made every row claim a false author."""
    app = create_app(make_settings(), scorer=scorer, sink=sink)
    client = app.test_client()
    client.post("/v1/moderate", json={"content": "An ordinary sentence.",
                                      "content_type": "article", "content_id": "post-42"})

    row = _store(app).get_moderation_logs()["logs"][0]
    assert row["content_id"] == "post-42"
    assert row["user_id"] == "anonymous"
    assert row["user_id"] != row["content_id"]


def test_a_supplied_user_id_is_recorded(make_settings, scorer, sink):
    """When something does supply an author, the log keeps it rather than overwriting it."""
    app = create_app(make_settings(), scorer=scorer, sink=sink)
    store = _store(app)
    store.record_moderation_log({"request_id": "r-1", "decision": "allow",
                                 "content_type": "article", "content_id": "post-7",
                                 "user_id": "u-99", "content": "text"})
    row = store.get_moderation_log_by_request_id("r-1")
    assert row["user_id"] == "u-99"
