"""Bulk select/deselect must act on the pictures the admin can see, and a retrain must say
what it did to them.

THE BUG THIS COMES FROM. An admin set out to make the gym pictures refuse. They searched
"gym", deselected what came up, retrained, and gym images published - as expected. Then they
selected the gym pictures again, retrained, and gym images still published. From the outside
that is a retrain that does not work.

Two things were actually happening, and neither was visible on the screen:

  * Select All / Deselect All sent only the category filter, never the search box. There is no
    `gym` category - gym pictures live under `physique` (shirtless physique display, blocked)
    and `safe` (gym adverts and class photos, allowed on purpose) - so the buttons acted on a
    different set of pictures than the grid was showing.

  * Searching "gym" returns 64 pictures of which 49 are `allow` examples. "Select all the gym
    images and retrain" therefore teaches both directions at once and mostly teaches ALLOW,
    and nothing in a grid of thumbnails says so.

And the retrain reported one number - training accuracy, measured in-sample, averaged over
every category, describing the probe alone while three channels decide - so there was no way
to find out that the category in question was at 98.6% with two pictures contradicting their
own labels.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.system_store import SystemStore  # noqa: E402


@pytest.fixture
def store(tmp_path):
    # No images_dir, so the store seeds nothing from images/ and these six rows are the
    # whole world. The seeded set is what the bug was found in, but a test that depends on
    # it would pass or fail on which folders happen to be on disk.
    s = SystemStore(path=tmp_path / "sel.db", images_dir=tmp_path / "no-images")
    # The shape that caused the confusion: one subject, two categories, two verdicts, and a
    # search term that spans both.
    s.add_image(filename="gym_shirtless_1.jpg", file_path="images/gym_neg/a.jpg",
                category="physique", action="block", is_selected=1)
    s.add_image(filename="gym_shirtless_2.jpg", file_path="images/gym_neg/b.jpg",
                category="physique", action="block", is_selected=1)
    s.add_image(filename="gym_advert_1.jpg", file_path="images/gym_positive/c.jpg",
                category="safe", action="allow", is_selected=1)
    s.add_image(filename="gym_advert_2.jpg", file_path="images/gym_positive/d.jpg",
                category="safe", action="allow", is_selected=1)
    # Something the search must NOT touch, in one of the same categories.
    s.add_image(filename="pistol.jpg", file_path="images/weapons/e.jpg",
                category="weapon", action="block", is_selected=1)
    s.add_image(filename="office_desk.jpg", file_path="images/allow/f.jpg",
                category="safe", action="allow", is_selected=1)
    return s


def selected(store) -> set[str]:
    return {i["filename"] for i in store.get_selected_for_training()}


# ---- the bug itself ------------------------------------------------------------------

def test_deselecting_a_search_leaves_everything_else_selected(store):
    """The whole bug in one assertion: "deselect all gym images" must deselect gym images."""
    n = store.set_selection_bulk(False, category="all", search="gym")
    assert n == 4
    assert selected(store) == {"pistol.jpg", "office_desk.jpg"}


def test_deselecting_a_search_does_not_empty_the_category(store):
    """`safe` holds two gym adverts and one desk. Deselecting the search must not take the
    desk with it - that is the step that made the following retrain measure something else."""
    store.set_selection_bulk(False, category="safe", search="gym")
    assert "office_desk.jpg" in selected(store)
    assert "gym_advert_1.jpg" not in selected(store)


def test_selecting_one_side_of_a_search_is_possible(store):
    """The way out of teaching both directions: filter to the blocked side and select that."""
    store.set_selection_bulk(False, category="all", search="gym")
    n = store.set_selection_bulk(True, category="all", search="gym", action="block")
    assert n == 2
    assert selected(store) == {"pistol.jpg", "office_desk.jpg",
                               "gym_shirtless_1.jpg", "gym_shirtless_2.jpg"}


def test_no_filter_still_means_everything(store):
    """The old behaviour has to survive, because it is what the button means with no filter."""
    assert store.set_selection_bulk(False) == 6
    assert selected(store) == set()


def test_bulk_selection_matches_what_the_grid_lists(store):
    """The real guarantee: the button and the grid must agree on the set, whatever the filter.
    Checked by running both and comparing, rather than by trusting two similar SQL strings."""
    for kwargs in ({"search": "gym"},
                   {"search": "gym", "action": "block"},
                   {"category": "safe", "search": "gym"},
                   {"category": "physique"},
                   {"action": "allow"},
                   {}):
        store.set_selection_bulk(True)                      # start from all selected
        store.set_selection_bulk(False, **kwargs)
        listed = store.list_images(limit=100, **kwargs)["items"]
        deselected = {i["filename"] for i in listed if not i["is_selected"]}
        assert deselected == {i["filename"] for i in listed}, kwargs
        still_on = selected(store)
        assert not (still_on & {i["filename"] for i in listed}), kwargs


# ---- making the composition visible ---------------------------------------------------

def test_the_filter_reports_which_way_it_teaches(store):
    """A search for "gym" spans two verdicts. The counts say so, because a grid cannot."""
    f = store.list_images(search="gym", limit=100)["filtered"]
    assert f["total"] == 4
    assert f["teaching_allow"] == 2
    assert f["teaching_block"] == 2


def test_filtered_counts_are_not_the_global_ones(store):
    """The stat boxes are global and were the only numbers on the page. If `filtered` ever
    collapses back onto them this test fails rather than the admin finding out."""
    res = store.list_images(search="gym", limit=100)
    assert res["filtered"]["total"] == 4
    assert res["stats"]["total"] == 6


def test_deselected_pictures_do_not_count_as_teaching(store):
    """`teaching_*` is about the training set, not about what is on screen: a picture nobody
    selected teaches nothing, however it is marked."""
    store.set_selection_bulk(False, category="all", search="gym", action="allow")
    f = store.list_images(search="gym", limit=100)["filtered"]
    assert f["total"] == 4 and f["selected"] == 2
    assert f["teaching_allow"] == 0
    assert f["teaching_block"] == 2
