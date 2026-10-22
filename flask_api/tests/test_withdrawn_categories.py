"""Deselecting every picture of a category withdraws it from the image model.

THE ASK. "When I deselect all the gym images and retrain, it should not train on the whole
physique category even if it was initially trained on blocking those images. It will then
check for policy rules, and if there is no policy it will by default allow those images."

WHY RETRAINING ALONE CANNOT DO THAT. Two channels keep an opinion the training set no longer
holds:

  * the probe cannot name a category. It scores its own classes, and an admin's category
    dissolves into them, so a gym photograph with every physique image deselected still reads
    as `revealing` and is still refused.

  * an exemplar is a separate mark. `is_selected` says "train on this"; `is_exemplar` says
    "refuse things that look like this". Deselecting clears the first and not the second.
    Measured on the real corpus: after all 72 gym photographs were deselected and the model
    retrained, 71 of them were still refused - by three exemplars nobody had taken back.

So a category with pictures but none selected is recorded as WITHDRAWN when the probe is
written, and clip.py honours it in both channels. What is left is the written policy, and a
category with no refusing rule publishes.

The test that matters most is the last one: withdrawing a category must not quieten any
other. A withdrawal that leaks is just a way of turning the model off.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.clip import VisualCheck, VisualConfig, VisualResult  # noqa: E402
from app.system_store import SystemStore  # noqa: E402

MODEL = ROOT / "models" / "img_v1"


@pytest.fixture
def store(tmp_path):
    return SystemStore(path=tmp_path / "w.db", images_dir=tmp_path / "no-images")


# ---- which categories count as withdrawn ------------------------------------------------

def test_a_category_with_nothing_selected_is_withdrawn(store):
    store.add_image(filename="g1.png", file_path="x/1.png", category="physique",
                    action="block", is_selected=0)
    store.add_image(filename="g2.png", file_path="x/2.png", category="physique",
                    action="block", is_selected=0)
    store.add_image(filename="w1.png", file_path="x/3.png", category="weapon",
                    action="block", is_selected=1)
    assert store.withdrawn_categories() == ["physique"]


def test_one_selected_picture_is_enough_to_keep_a_category(store):
    """Withdrawal is "the admin removed all of them", not "most of them"."""
    store.add_image(filename="g1.png", file_path="x/1.png", category="physique",
                    action="block", is_selected=0)
    store.add_image(filename="g2.png", file_path="x/2.png", category="physique",
                    action="block", is_selected=1)
    assert store.withdrawn_categories() == []


def test_a_category_with_no_pictures_is_not_withdrawn(store):
    """A label an admin wrote a rule for but never added pictures to was never trained on, so
    there is nothing to withdraw - and calling it withdrawn would silence the probe for a
    category the admin has said nothing about."""
    store.add_image(filename="w1.png", file_path="x/1.png", category="weapon",
                    action="block", is_selected=1)
    assert "rainbow" not in store.withdrawn_categories()
    assert store.withdrawn_categories() == []


def test_deselecting_and_reselecting_is_a_round_trip(store):
    store.add_image(filename="g1.png", file_path="x/1.png", category="physique",
                    action="block", is_selected=1)
    assert store.withdrawn_categories() == []
    store.set_selection_bulk(False, category="physique")
    assert store.withdrawn_categories() == ["physique"]
    store.set_selection_bulk(True, category="physique")
    assert store.withdrawn_categories() == []


# ---- the decision honours it -------------------------------------------------------------

def config():
    return VisualConfig(enabled=True, model_dir=str(MODEL))


def result(**kw):
    base = dict(checked=True, label="physique", category="", score=0.9,
                probe_scores={"safe": 0.1, "revealing": 0.9}, probe_unsafe=0.9)
    base.update(kw)
    return VisualResult(**base)


def test_the_probe_cannot_refuse_a_withdrawn_category():
    """0.9 is far over the 0.70 bar. Withdrawn, it still does not refuse."""
    vc = VisualCheck(config())
    assert vc.decides_reject(result(withdrawn_category="")) is True
    assert vc.decides_reject(result(withdrawn_category="physique")) is False


def test_the_record_says_the_probe_was_withheld_and_why():
    """A probe reading of 0.9 beside a published picture looks like a bug unless the record
    says the probe was stood down. The cloud-and-rainbow case took an hour for want of
    exactly this."""
    d = result(withdrawn_category="physique").as_dict()
    assert d["visual"]["probe"]["unsafe"] == 0.9
    assert d["visual"]["probe"]["withheld"] == "physique"
    assert "withheld" not in result().as_dict()["visual"]["probe"]


def test_refused_by_agrees_with_the_decision():
    """These two are read together in the audit trail, so they must not disagree about
    whether the probe had a vote."""
    cfg = config()
    assert result().refused_by(cfg) == "probe"
    assert result(withdrawn_category="physique").refused_by(cfg) is None


def test_a_withdrawn_category_still_loses_to_a_written_rule():
    """"It will then check for policy rules" - withdrawing the category does not overrule a
    rule that refuses the picture on other grounds. Only the probe is stood down."""
    vc = VisualCheck(config())
    r = result(withdrawn_category="physique", label="sexual", category="sexual_content",
               score=0.95, scores={"sexual": 0.95})
    assert vc.decides_reject(r) is True
    assert r.refused_by(config()) == "policy"


def test_an_allow_exemplar_still_wins():
    """The allow side is an override and is checked before anything else. Withdrawal must not
    quietly change what "allow things like this" means."""
    vc = VisualCheck(config())
    r = result(withdrawn_category="physique", exemplar_allow=0.9, exemplar_block=0.1)
    assert vc.decides_reject(r) is False


# ---- the exemplar channel ----------------------------------------------------------------

@pytest.fixture
def visual():
    vc = VisualCheck(config())
    if not vc.ready:
        pytest.skip("the image model is not present in this checkout")
    return vc


def png(colour, size=(96, 96)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


def embed(visual, colour):
    from PIL import Image
    im = Image.open(io.BytesIO(png(colour))).convert("RGB")
    e = visual._session.run([visual._image_output],
                            {"pixel_values": visual.preprocess(im)})[0][0]
    return (e / np.linalg.norm(e)).astype(np.float32)


def test_an_exemplar_of_a_withdrawn_category_stops_matching(visual):
    """The channel that actually kept the gym photographs refused."""
    e = embed(visual, (180, 90, 40))
    visual._exemplars = (e[None, :], ["block"], ["physique"], ["7"])

    visual._withdrawn = set()
    block, _allow, who = visual.match_exemplars(e)
    assert block > 0.9 and who["category"] == "physique"

    visual._withdrawn = {"physique"}
    block, _allow, who = visual.match_exemplars(e)
    assert block == 0.0 and who is None


def test_withdrawing_one_category_leaves_another_category_s_exemplars_alone(visual):
    """THE TEST THAT MATTERS. A withdrawal that reaches past its own category is not a
    withdrawal, it is a way of turning the model off - and `rainbow`, `parties` and `toy` are
    marks the admin still wants enforced."""
    e = embed(visual, (40, 140, 200))
    visual._exemplars = (np.vstack([e, e]), ["block", "block"],
                         ["physique", "rainbow"], ["7", "8"])

    visual._withdrawn = {"physique"}
    block, _allow, who = visual.match_exemplars(e)
    assert block > 0.9, "the rainbow exemplar must still match"
    assert who["category"] == "rainbow"


def test_the_match_is_on_the_category_not_the_name_s_spelling(visual):
    """Categories arrive from the database in whatever case they were typed."""
    e = embed(visual, (90, 90, 160))
    visual._exemplars = (e[None, :], ["block"], ["Physique"], ["7"])
    visual._withdrawn = {"physique"}
    block, _allow, who = visual.match_exemplars(e)
    assert block == 0.0 and who is None


# ---- bundles written before withdrawal existed -------------------------------------------

def test_a_probe_written_before_withdrawal_existed_still_loads(tmp_path):
    """Every probe.npz already on disk lacks the key. Missing must mean "nothing withdrawn" -
    the behaviour those bundles already had - and must not stop the probe loading, because a
    probe that fails to load is a check that silently stops checking."""
    old = tmp_path / "probe.npz"
    np.savez(old, W=np.zeros((2, 512), dtype=np.float32), b=np.zeros(2, dtype=np.float32),
             classes=np.array(["safe", "weapon"]), dim=np.array(512))
    with np.load(old, allow_pickle=False) as pr:
        assert VisualCheck._read_withdrawn(pr) == set()


def test_a_bundle_that_withdrew_nothing_reads_as_nothing(tmp_path):
    p = tmp_path / "probe.npz"
    np.savez(p, W=np.zeros((2, 512), dtype=np.float32), b=np.zeros(2, dtype=np.float32),
             classes=np.array(["safe", "weapon"]), dim=np.array(512),
             withdrawn=np.array([], dtype="<U1"))
    with np.load(p, allow_pickle=False) as pr:
        assert VisualCheck._read_withdrawn(pr) == set()


def test_the_withdrawal_survives_a_round_trip_through_the_bundle(tmp_path):
    """It travels with the probe rather than being recomputed at request time, so a restart
    keeps it - the admin withdrew a category, not a process."""
    p = tmp_path / "probe.npz"
    np.savez(p, W=np.zeros((2, 512), dtype=np.float32), b=np.zeros(2, dtype=np.float32),
             classes=np.array(["safe", "weapon"]), dim=np.array(512),
             withdrawn=np.array(["physique", "cloud"]))
    with np.load(p, allow_pickle=False) as pr:
        assert VisualCheck._read_withdrawn(pr) == {"physique", "cloud"}
