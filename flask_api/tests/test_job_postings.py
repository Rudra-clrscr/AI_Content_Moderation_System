"""Job postings, and the DTO fields a forwarded posting arrives in.

A job posting is the surface where fee-for-job and fake-recruitment scams live, and they read
like ordinary hiring copy right up to the sentence asking for money - which is rarely in the
title. `CreateJobDTO` spreads that prose over eight boxes, so a moderator that read only
`Description` would miss the one line that matters.
"""
import json
from pathlib import Path

import pytest

from app import create_app
from app.api import PART_ALIASES, ATTACHMENT_FIELDS
from app.pipeline import ContentType
from tests.conftest import FixedScorer

SWAGGER = Path(__file__).resolve().parent.parent.parent / "bonc_swagger.json"

JOB = {
    "Title": "Warehouse Supervisor",
    "CompanyName": "Fortune Agro Exports",
    "Description": "Oversee inbound and outbound shipments at our Pune facility.",
    "Responsibilities": "Plan shifts, maintain stock accuracy, report weekly.",
    "Requirements": "Three years in logistics.",
    "Qualification": "Graduate in any discipline.",
    "KeySkills": "inventory, SAP, team handling",
    "Benefits": "PF, health cover, annual bonus",
    # Pickers and structured values. Not member-written prose; never joined.
    "EmploymentType": "Full-time", "WorkMode": "On-site", "City": "Pune",
    "Country": "India", "JobCategory": "Logistics", "Status": "Draft",
    "content_type": "job", "content_id": "JOB-4412",
}


def client(make_settings, risk=0.01):
    return create_app(make_settings(max_chars=25_000),
                      scorer=FixedScorer(risk)).test_client()


def test_job_is_a_surface_the_service_accepts(make_settings):
    assert ContentType("job") is ContentType.JOB
    r = client(make_settings).post("/v1/moderate", json=JOB)
    assert r.status_code == 200


def test_every_prose_box_of_a_job_posting_is_judged(make_settings):
    data = client(make_settings).post("/v1/moderate", json=JOB).get_json()["Data"]
    assert sorted(data["fields"]) == [
        "benefits", "body", "company", "key_skills",
        "qualification", "requirements", "responsibilities", "title",
    ]
    for value in ("Warehouse Supervisor", "Fortune Agro Exports", "Oversee inbound",
                  "Plan shifts", "Three years in logistics", "Graduate in any discipline",
                  "inventory, SAP", "PF, health cover"):
        assert value in data["content"], f"{value!r} was not moderated"


def test_the_pickers_are_not_joined(make_settings):
    """A dropdown is not member-written text. Scoring `Full-time` helps nobody."""
    content = client(make_settings).post("/v1/moderate", json=JOB).get_json()["Data"]["content"]
    for picker in ("Full-time", "On-site", "Logistics", "Draft"):
        assert picker not in content


def test_a_scam_buried_in_requirements_is_still_read(make_settings):
    """`badword` is a block rule in the test gate, and it is nowhere near the title.

    This is the shape of the real thing: everything reads like a normal posting until one
    line deep in the requirements. A moderator that joined only title and description would
    answer approved.
    """
    posting = {**JOB, "Requirements": "Three years in logistics. badword."}
    data = client(make_settings).post("/v1/moderate", json=posting).get_json()["Data"]
    assert data["Action"] == "reject"


def test_the_author_is_addressed_as_a_job_posting(make_settings):
    """Every surface has its own noun, or the author is told their *post* needs changes."""
    posting = {**JOB, "Requirements": "badword."}
    fb = client(make_settings).post("/v1/moderate", json=posting).get_json()["Data"]["feedback"]
    assert "job posting" in fb["title"].lower() or "job posting" in fb["message"].lower()


# ---- the guard against the company's DTOs drifting away from us ---------------------------

# Fields in the company's DTOs that carry no member-written prose: identifiers, pickers,
# structured values, and the attachment lists handled separately. Anything NOT here and not
# mapped in PART_ALIASES is text that would be silently dropped.
NOT_PROSE = {
    "UserId", "GroupId", "Id", "Status", "Visibility", "ArticleType", "PostType",
    "PublishedBy", "IsSeoEnabled", "SortOrder", "JobType", "JobCategory", "JobFunction",
    "EmploymentType", "WorkMode", "SalaryPeriod", "Department", "ReportingTo",
    "City", "State", "Country", "ApplicationEmail", "ApplicationUrl", "MediaUrl",
    "MediaType", "Filename",
}


@pytest.mark.parametrize("dto", ["CreateArticleDto", "CreateNetworkArticleDto",
                                 "CreatePostDTO", "CreateGroupPostDto", "CreateJobDTO"])
def test_no_prose_field_of_a_company_dto_is_silently_dropped(dto):
    """Every text field of a forwarded DTO is moderated, an attachment, or explicitly not prose.

    A field this service does not know about is not an error anyone sees: it is simply
    ignored, and the text goes unchecked while the response says approved. That is how
    `Title` and then `TextContent` were both missed, so the list is checked against the
    company specification rather than maintained by memory.
    """
    if not SWAGGER.exists():                                      # pragma: no cover
        pytest.skip("bonc_swagger.json not present")
    schemas = json.loads(SWAGGER.read_text(encoding="utf-8"))["components"]["schemas"]
    props = (schemas.get(dto) or {}).get("properties") or {}
    known = {alias for aliases in PART_ALIASES.values() for alias in aliases}

    unhandled = [
        name for name, spec in props.items()
        if spec.get("type") == "string" and not spec.get("format")
        and name not in known and name not in ATTACHMENT_FIELDS and name not in NOT_PROSE
    ]
    assert not unhandled, (
        f"{dto} has text fields this service would silently ignore: {sorted(unhandled)}.\n"
        f"Map them in api.PART_ALIASES, or add them to NOT_PROSE if they carry no "
        f"member-written text."
    )
