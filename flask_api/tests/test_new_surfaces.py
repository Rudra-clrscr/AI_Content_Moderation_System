"""The two surfaces the platform publishes from that the API did not name.

BONC's own endpoints are `/api/Post/createPost` and `/api/GroupPost/CreateGroupPost`, and
`/api/Article/CreateArticle` and `/api/NetworkArticle/CreateArticle`. Until `group_post` and
`network_article` existed, half of that traffic had to be sent as `post` or `article`, which
meant an admin could not moderate a group post differently from an open-feed one and the
audit log could not tell the two apart after the fact.

These pin the three things adding a surface has to get right: the API accepts it, the author
is addressed in the right words, and an admin can set a level for it on its own.
"""
from __future__ import annotations

import pytest

from app import create_app
from app.pipeline import ContentType

NEW = ("group_post", "network_article")


@pytest.mark.parametrize("content_type", NEW)
def test_the_api_accepts_the_surface(client, content_type):
    r = client.post("/v1/moderate", json={"content": "Our mill ships nationwide.",
                                          "content_type": content_type})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["Data"]["content_type"] == content_type


@pytest.mark.parametrize("content_type", NEW)
def test_the_surface_is_a_real_enum_member(content_type):
    """A string the API happens to accept is not the same as a surface the rest of the
    service knows about: the level table, the audit filter and the feedback nouns are all
    built from ContentType, so a surface that is not a member is invisible to them."""
    assert ContentType(content_type).value == content_type


@pytest.mark.parametrize("content_type", NEW)
def test_a_level_can_be_set_for_the_surface_on_its_own(client, content_type):
    r = client.put(f"/v1/admin/moderation-levels/{content_type}",
                   json={"level": "strict"}, headers={"X-Admin-Token": "secret"})
    assert r.status_code == 200, r.get_data(as_text=True)
    table = client.get("/v1/admin/moderation-levels",
                       headers={"X-Admin-Token": "secret"}).get_json()
    assert table["features"][content_type]["level"] == "strict"
    # the surface it was split off from must not have moved with it
    sibling = "post" if content_type == "group_post" else "article"
    assert sibling not in table["features"]


def test_group_post_is_distinguishable_from_post_in_the_result(client):
    """The reason the surface exists: the stored decision has to say which one it was."""
    bodies = {}
    for ct in ("post", "group_post"):
        bodies[ct] = client.post("/v1/moderate",
                                 json={"content": "Join our group for bulk cotton rates.",
                                       "content_type": ct}).get_json()["Data"]
    assert bodies["post"]["content_type"] == "post"
    assert bodies["group_post"]["content_type"] == "group_post"


def test_the_grievance_surfaces_still_exclude_the_new_ones():
    """Documented in docs/API_REFERENCE.md 12.5, and asserted so it cannot change unnoticed.

    `group_post` and `network_article` are public surfaces, so the complaint cover arguably
    belongs on them - but that list is the moderation-levels work's to change, and a silent
    change either way is the thing worth catching. If this fails because the new surfaces
    were added deliberately, delete the test and the gap note together.
    """
    from app.grievance import DEFAULT_SURFACES
    assert set(DEFAULT_SURFACES) == {"article", "post", "advertisement"}
