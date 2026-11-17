"""The moderation log's numbers must describe the rows on the screen, per model, per window.

THE BUGS THIS COMES FROM. The KPI cards over the moderation log were counted across the whole
table whatever the filters said, so narrowing the log to one surface or one week left
"Allowed" and "Refusals" showing all-time totals beside rows that did not add up to them. A
picture's decision could not be told from an article's - nothing in the row said which model
had decided - so there was no honest way to say how many images were refused. And no member
was ever recorded: every row read "anonymous", so a member refused ten times in a day looked
exactly like ten members refused once.
"""
from __future__ import annotations

import io
import time

import pytest

ADMIN = {"X-Admin-Token": "secret"}


def _row(store, n, *, decision, modality="text", user="u1", content_type="post", at=None):
    for i in range(n):
        store.record_moderation_log({
            "request_id": f"{user}-{modality}-{decision}-{i}-{time.time_ns()}",
            "content_type": content_type, "decision": decision, "modality": modality,
            "user_id": user, "content": "x"})
    if at is not None:
        with store._tx() as db:
            db.execute("UPDATE moderation_log SET created_at = ? WHERE created_at > ?",
                       (at, at))


@pytest.fixture
def store(client):
    return client.application.extensions["admin"].store


def test_kpis_split_by_model_and_follow_the_filters(client, store):
    _row(store, 3, decision="allow", modality="text")
    _row(store, 2, decision="reject", modality="text")
    _row(store, 4, decision="allow", modality="image", content_type="product_listing")
    _row(store, 1, decision="reject", modality="image", content_type="product_listing")

    d = client.get("/v1/admin/moderation-logs", headers=ADMIN).get_json()
    assert d["stats"]["total"] == 10
    assert d["by_modality"]["text"] == {"total": 5, "allow": 3, "revise": 0, "reject": 2,
                                        "reject_rate": 0.4}
    assert d["by_modality"]["image"]["reject"] == 1
    assert d["by_modality"]["image"]["allow"] == 4

    # A surface filter narrows the cards too, not only the table.
    d = client.get("/v1/admin/moderation-logs?content_type=product_listing",
                   headers=ADMIN).get_json()
    assert d["stats"]["total"] == 5 and d["by_modality"]["text"]["total"] == 0

    # The model filter.
    d = client.get("/v1/admin/moderation-logs?modality=image", headers=ADMIN).get_json()
    assert d["total"] == 5 and all(r["modality"] == "image" for r in d["logs"])

    # The decision filter narrows the table but leaves the split on the cards.
    d = client.get("/v1/admin/moderation-logs?decision=reject", headers=ADMIN).get_json()
    assert d["total"] == 3
    assert d["stats"]["allow"] == 7 and d["stats"]["reject"] == 3


def test_date_range_is_exact(client, store):
    old = time.time() - 10 * 86400
    _row(store, 2, decision="reject", at=old)
    _row(store, 1, decision="allow")
    since = time.time() - 86400
    d = client.get(f"/v1/admin/moderation-logs?since={since}", headers=ADMIN).get_json()
    assert d["stats"]["total"] == 1
    # ISO form, as the screen sends it.
    from datetime import datetime, timezone
    iso = datetime.fromtimestamp(since, timezone.utc).isoformat().replace("+00:00", "Z")
    d = client.get(f"/v1/admin/moderation-logs?since={iso}", headers=ADMIN).get_json()
    assert d["stats"]["total"] == 1
    d = client.get(f"/v1/admin/moderation-logs?until={since}", headers=ADMIN).get_json()
    assert d["stats"]["total"] == 2


def test_repeat_offenders_need_both_a_count_and_a_rate(client, store):
    _row(store, 4, decision="reject", user="spammer")
    _row(store, 1, decision="allow", user="spammer")
    # Busy and mostly fine: many refusals, low rate. Not an offender.
    _row(store, 4, decision="reject", user="busy")
    _row(store, 40, decision="allow", user="busy")
    # One post, refused: a high rate on one data point. Not an offender.
    _row(store, 1, decision="reject", user="newcomer")
    # Unattributable rows never count.
    _row(store, 9, decision="reject", user="anonymous")

    d = client.get("/v1/admin/moderation-logs", headers=ADMIN).get_json()
    users = {u["user_id"]: u for u in d["repeat_offenders"]["users"]}
    assert set(users) == {"spammer"}
    assert users["spammer"]["rejected"] == 4 and users["spammer"]["total"] == 5

    d = client.get("/v1/admin/moderation-logs?offender_min=1&offender_rate=1",
                   headers=ADMIN).get_json()
    assert {u["user_id"] for u in d["repeat_offenders"]["users"]} == {"newcomer"}


def test_submissions_record_the_member_and_the_model(client, store):
    client.post("/v1/moderate", json={"content": "hello there", "content_type": "post",
                                      "userId": "m-42"})
    client.post("/v1/moderate", json={"content": "hello again", "content_type": "post"},
                headers={"X-User-Id": "m-43"})
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), (10, 200, 10)).save(buf, "PNG")
    buf.seek(0)
    client.post("/v1/moderate/media", data={"file": (buf, "green.png"), "content_type": "post",
                                            "user_id": "m-44"},
                content_type="multipart/form-data")

    rows = {r["user_id"]: r for r in
            client.get("/v1/admin/moderation-logs", headers=ADMIN).get_json()["logs"]}
    assert rows["m-42"]["modality"] == "text"
    assert rows["m-43"]["modality"] == "text"
    assert rows["m-44"]["modality"] == "image"


def test_old_rows_are_backfilled_with_a_model(tmp_path):
    """Rows written before `modality` existed: a picture's own verdict is recognisable."""
    import sqlite3
    from app.admin_store import AdminStore

    path = tmp_path / "old.db"
    AdminStore(path)  # create the schema, then strip the column back out as an old file had
    db = sqlite3.connect(path)
    db.execute("DROP INDEX idx_mod_log_modality")
    db.execute("ALTER TABLE moderation_log DROP COLUMN modality")
    db.execute("INSERT INTO moderation_log (request_id, content_type, decision, gate_matches, "
               "content_preview, created_at, decided_at) VALUES "
               "('a', 'post', 'reject', '[{\"rule_id\": \"visual.weapon\"}]', '[gun.jpg]', 1, 'x'),"
               "('b', 'post', 'allow', '[]', '[photo.png]', 1, 'x'),"
               "('c', 'post', 'allow', '[]', 'an ordinary sentence', 1, 'x')")
    db.commit()
    db.close()
    store = AdminStore(path)
    with store._tx() as db:
        got = dict(db.execute("SELECT request_id, modality FROM moderation_log").fetchall())
    assert got == {"a": "image", "b": "image", "c": "text"}


def test_health_reports_each_check(client):
    d = client.get("/v1/admin/health", headers=ADMIN).get_json()
    names = {c["name"]: c for c in d["checks"]}
    assert "Text moderation model" in names and "Image moderation model" in names
    assert names["Text moderation model"]["status"] == "ok"
    assert d["status"] in ("ok", "degraded")
    assert set(d["traffic"]["last_hour"]) >= {"text", "image", "last_decision_at"}


def test_health_needs_an_admin(client):
    assert client.get("/v1/admin/health").status_code == 401


def test_repeat_offenders_endpoint(client, store):
    _row(store, 4, decision="reject", user="spammer", modality="image")
    _row(store, 1, decision="allow", user="spammer")
    _row(store, 1, decision="reject", user="newcomer")
    d = client.get("/v1/admin/moderation-logs/repeat-offenders", headers=ADMIN).get_json()
    assert [u["user_id"] for u in d["users"]] == ["spammer"]
    assert d["users"][0]["image_rejected"] == 4
    assert (d["min_rejects"], d["min_rate"]) == (3, 0.5)

    # The rule travels in the query, under either name.
    d = client.get("/v1/admin/moderation-logs/repeat-offenders?min_rejects=1&min_rate=1",
                   headers=ADMIN).get_json()
    assert [u["user_id"] for u in d["users"]] == ["newcomer"]
    d = client.get("/v1/admin/moderation-logs/repeat-offenders?modality=text",
                   headers=ADMIN).get_json()
    assert d["users"] == []

    # A window that ends before anything was written finds no one.
    d = client.get("/v1/admin/moderation-logs/repeat-offenders?until=1", headers=ADMIN).get_json()
    assert d["users"] == []


def test_member_record_answers_yes_or_no_with_the_numbers(client, store):
    _row(store, 3, decision="reject", user="m-1")
    _row(store, 1, decision="allow", user="m-1")
    _row(store, 1, decision="reject", user="m-2")
    _row(store, 9, decision="allow", user="m-2")

    d = client.get("/v1/admin/moderation-logs/repeat-offenders/m-1", headers=ADMIN).get_json()
    assert d["is_repeat_offender"] is True
    assert (d["total"], d["rejected"], d["allowed"], d["reject_rate"]) == (4, 3, 1, 0.75)

    d = client.get("/v1/admin/moderation-logs/repeat-offenders/m-2", headers=ADMIN).get_json()
    assert d["is_repeat_offender"] is False and d["rejected"] == 1

    d = client.get("/v1/admin/moderation-logs/repeat-offenders/nobody", headers=ADMIN).get_json()
    assert d["total"] == 0 and d["is_repeat_offender"] is False and d["reject_rate"] is None

    r = client.get("/v1/admin/moderation-logs/repeat-offenders/anonymous", headers=ADMIN)
    assert r.status_code == 400


def test_repeat_offender_endpoints_need_an_admin(client):
    assert client.get("/v1/admin/moderation-logs/repeat-offenders").status_code == 401
    assert client.get("/v1/admin/moderation-logs/repeat-offenders/m-1").status_code == 401


# ---- blocking a member ---------------------------------------------------------------

def _post(client, user=None, **kw):
    headers = {"X-User-Id": user} if user else {}
    return client.post("/v1/moderate", json={"content": "hello there", "content_type": "post", **kw},
                       headers=headers)


def test_a_blocked_member_is_refused_wherever_they_post_from(client, store):
    r = client.post("/v1/admin/blocked-clients", headers=ADMIN,
                    json={"value": "spammer", "scope": "member", "reason": "scam listings"})
    assert r.status_code == 201, r.get_json()

    r = _post(client, "spammer")
    assert r.status_code == 403
    assert r.get_json()["Data"]["code"] == "member_blocked"
    # The id in the body counts as much as the header.
    assert _post(client, userId="spammer").status_code == 403
    # Everyone else, and anonymous submissions, carry on.
    assert _post(client, "someone-else").status_code == 200
    assert _post(client).status_code == 200

    r = client.delete("/v1/admin/blocked-clients/spammer?scope=member", headers=ADMIN)
    assert r.status_code == 200
    assert _post(client, "spammer").status_code == 200


def test_a_member_id_that_reads_like_an_address_blocks_only_the_member(client):
    client.post("/v1/admin/blocked-clients", headers=ADMIN,
                json={"value": "127.0.0.1", "scope": "member"})
    assert _post(client).status_code == 200            # the test client's own address
    assert _post(client, "127.0.0.1").status_code == 403


def test_blocked_members_are_marked_on_the_log_and_the_offender_lists(client, store):
    _row(store, 4, decision="reject", user="spammer")
    client.post("/v1/admin/blocked-clients", headers=ADMIN,
                json={"value": "spammer", "scope": "member"})
    d = client.get("/v1/admin/moderation-logs", headers=ADMIN).get_json()
    assert d["blocked_members"] == ["spammer"]
    assert d["repeat_offenders"]["users"][0]["blocked"] is True
    d = client.get("/v1/admin/moderation-logs/repeat-offenders", headers=ADMIN).get_json()
    assert d["users"][0]["blocked"] is True
    d = client.get("/v1/admin/moderation-logs/repeat-offenders/spammer", headers=ADMIN).get_json()
    assert d["blocked"] is True


def test_anonymous_cannot_be_blocked_as_a_member(client):
    r = client.post("/v1/admin/blocked-clients", headers=ADMIN,
                    json={"value": "anonymous", "scope": "member"})
    assert r.status_code == 400 and r.get_json()["Data"]["code"] == "bad_member"
