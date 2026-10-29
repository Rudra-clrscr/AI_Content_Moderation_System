"""Downloading the moderation log as text, Word or Excel.

An export is read by somebody who was not at the screen that made it, often months later, so
the two things that matter most are not the formatting: the file must say WHICH ROWS it
contains - the filter and the counts - and it must carry the same set the screen was showing.
A file that silently holds a different set than the page it came from is the kind of mistake
nobody checks a download for.
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.admin_store import AdminStore  # noqa: E402
from app.log_export import render, to_doc, to_txt, to_xlsx  # noqa: E402

COLUMNS = AdminStore.EXPORT_COLUMNS
HDR = {"X-Requested-With": "bonc-admin"}


def rows(n=3):
    return [{"created_at": 1793018846.53 + i, "request_id": f"req-{i}", "user_id": "anonymous",
             "content_id": f"c{i}", "content_type": "article",
             "decision": "reject" if i % 2 else "allow", "severity": "high",
             "risk_score": 0.996, "decided_by": "model", "predicted_label": "violation",
             "model_version": "bonc-v9-single", "latency_ms": 394.167,
             "client_ip": "203.0.113.7", "client_network": "203.0.113.0/24",
             "content_preview": f"row {i} दुकान",
             "feedback_title": "Your post can't be published",
             "feedback_message": "It appears to break our policies."} for i in range(n)]


# ---- the file says what it holds -----------------------------------------------------

def test_the_text_export_records_its_filter_and_counts():
    out = to_txt(rows(3), COLUMNS, total=8177, truncated=True,
                 filters={"decision": "reject", "search": "sofa"}).decode("utf-8")
    assert "decision=reject" in out and "search=sofa" in out
    assert "3 of 8177 matching" in out
    assert "truncated" in out.lower()


def test_an_unfiltered_export_says_so_rather_than_leaving_it_blank():
    out = to_txt(rows(1), COLUMNS, total=1, filters={}).decode("utf-8")
    assert "Filters:   none" in out


def test_a_complete_export_does_not_claim_to_be_truncated():
    out = to_txt(rows(2), COLUMNS, total=2, truncated=False, filters={}).decode("utf-8")
    assert "truncated" not in out.lower()


def test_an_empty_result_is_a_file_that_says_so():
    """Not a zero-byte download the operator has to guess about."""
    out = to_txt([], COLUMNS, total=0, filters={"decision": "revise"}).decode("utf-8")
    assert "no moderation events match this filter" in out


# ---- the formats themselves ----------------------------------------------------------

def test_timestamps_are_readable_rather_than_epoch_seconds():
    """1793018846.530235 is the single most useless thing a log export can contain."""
    out = to_txt(rows(1), COLUMNS, total=1).decode("utf-8")
    assert "1793018846" not in out
    assert "UTC" in out


def test_the_text_columns_line_up():
    body = [l for l in to_txt(rows(3), COLUMNS, total=3).decode("utf-8").split("\n")
            if l.startswith("2026-")]
    assert len(body) == 3
    # Every row puts the same column at the same offset - that is what "lines up" means, and
    # it is what a variable-width value in an earlier column breaks.
    assert len({l.index("article") for l in body}) == 1


def test_non_latin_content_survives_every_format():
    """Most of the previews in this corpus are Devanagari. A codepage guess turns the whole
    export into mojibake, and Word is the one that guesses."""
    r = rows(1)
    txt = to_txt(r, COLUMNS, total=1)
    doc = to_doc(r, COLUMNS, total=1)
    assert "दुकान".encode("utf-8") in txt
    assert doc.startswith(b"\xef\xbb\xbf"), "Word needs the BOM to read this as UTF-8"
    assert "दुकान".encode("utf-8") in doc


def test_the_word_export_is_a_document_word_will_open():
    out = to_doc(rows(2), COLUMNS, total=2).decode("utf-8-sig")
    assert "urn:schemas-microsoft-com:office:word" in out
    assert out.count("<tr") == 3                      # a heading row and two rows of data


def test_the_word_export_escapes_content_rather_than_rendering_it():
    r = rows(1)
    r[0]["content_preview"] = '<script>alert(1)</script>'
    out = to_doc(r, COLUMNS, total=1).decode("utf-8-sig")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_the_excel_export_is_a_real_workbook():
    data = to_xlsx(rows(3), COLUMNS, total=3)
    assert zipfile.is_zipfile(io.BytesIO(data)), "an .xlsx is a zip, not a renamed CSV"
    openpyxl = pytest.importorskip("openpyxl")
    ws = openpyxl.load_workbook(io.BytesIO(data)).active
    # Found rather than assumed: the preamble grows a line when the export is truncated.
    head_row = next(r for r in range(1, ws.max_row + 1)
                    if ws.cell(row=r, column=1).value == "Timestamp")
    assert [c.value for c in ws[head_row]][:3] == ["Timestamp", "Request ID", "User"]
    assert ws.freeze_panes == f"A{head_row + 1}", "the headings must stay put when scrolling"
    assert ws.auto_filter.ref.startswith(f"A{head_row}"), "the filter must sit ON the headings"


def test_numbers_are_numbers_so_a_sort_is_a_sort():
    """The whole reason this is a workbook and not a CSV: risk scores that sort as text put
    0.996 below 0.25."""
    openpyxl = pytest.importorskip("openpyxl")
    ws = openpyxl.load_workbook(io.BytesIO(to_xlsx(rows(2), COLUMNS, total=2))).active
    head_row = next(r for r in range(1, ws.max_row + 1)
                    if ws.cell(row=r, column=1).value == "Timestamp")
    labels = [c.value for c in ws[head_row]]
    risk = ws.cell(row=head_row + 1, column=labels.index("Risk score") + 1)
    assert isinstance(risk.value, float)


def test_an_unknown_format_is_refused_rather_than_guessed():
    with pytest.raises(ValueError):
        render("pdf", rows(1), COLUMNS, total=1)


def test_each_format_names_its_own_file():
    for fmt, ext in (("txt", ".txt"), ("doc", ".doc"), ("xlsx", ".xlsx")):
        _data, media_type, name = render(fmt, rows(1), COLUMNS, total=1)
        assert name.endswith(ext) and name.startswith("moderation-logs-")
        assert media_type


# ---- through the endpoint ------------------------------------------------------------

@pytest.fixture
def client(make_settings, tmp_path):
    store = AdminStore(path=tmp_path / "admin.db")
    for i in range(7):
        store.record_moderation_log({
            "request_id": f"r{i}", "content_type": "article",
            "decision": "reject" if i % 2 else "allow",
            "content": f"row {i}", "feedback": {"severity": "high" if i % 2 else "low"},
            "client": {"ip": "203.0.113.7", "network": "203.0.113.0/24"}})
    app = create_app(make_settings(admin_token="secret"), admin_store=store)
    app.config["_store"] = store
    return app.test_client()


def get(client, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(f"/v1/admin/moderation-logs/export?{qs}",
                      headers={**HDR, "X-Admin-Token": "secret"})


def test_the_export_needs_an_admin(client):
    assert client.get("/v1/admin/moderation-logs/export?format=txt").status_code == 401


@pytest.mark.parametrize("fmt", ["txt", "doc", "xlsx"])
def test_each_format_downloads(client, fmt):
    r = get(client, format=fmt)
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert "attachment;" in r.headers["Content-Disposition"]
    assert f".{fmt}" in r.headers["Content-Disposition"]
    assert r.data


def test_the_friendly_format_names_work_too(client):
    """"excel" and "word" are what the person asking for this calls them."""
    assert ".xlsx" in get(client, format="excel").headers["Content-Disposition"]
    assert ".doc" in get(client, format="word").headers["Content-Disposition"]


def test_an_unknown_format_is_a_message_not_a_crash(client):
    r = get(client, format="pdf")
    assert r.status_code == 400
    assert r.get_json()["error"] == "bad_format"


def test_the_export_covers_the_whole_filtered_set_not_one_page(client):
    """The screen shows fifteen rows; nobody wants fifteen rows in a spreadsheet."""
    r = get(client, format="txt")
    assert int(r.headers["X-Export-Rows"]) == 7


def test_the_export_obeys_the_filters_the_screen_is_showing(client):
    r = get(client, format="txt", decision="reject")
    assert int(r.headers["X-Export-Rows"]) == 3
    assert int(r.headers["X-Export-Total"]) == 3
    assert "decision=reject" in r.get_data(as_text=True)


def test_a_truncated_export_reports_both_numbers(client):
    r = get(client, format="txt", max_rows=2)
    assert int(r.headers["X-Export-Rows"]) == 2
    assert int(r.headers["X-Export-Total"]) == 7
    assert "2 of 7 matching" in r.get_data(as_text=True)


def test_the_address_is_in_the_export(client):
    """It is the column an abuse report is built around."""
    body = get(client, format="txt").get_data(as_text=True)
    assert "Device IP" in body and "203.0.113.7" in body
    assert "203.0.113.0/24" in body


def test_an_export_is_never_cached(client):
    """A point-in-time copy of a table that is still being written to."""
    assert get(client, format="txt").headers["Cache-Control"] == "no-store"


def test_the_export_route_is_not_shadowed_by_the_detail_route(client):
    """/moderation-logs/export sits beside /moderation-logs/<request_id>, and "export" is a
    perfectly good request id as far as the router is concerned."""
    r = get(client, format="txt")
    assert r.status_code == 200
    assert r.mimetype == "text/plain"
