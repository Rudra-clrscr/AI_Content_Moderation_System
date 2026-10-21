"""A retrain has to say what it did, and it has to say it from the code that decides.

WHY. The retrain reported training accuracy - 85.4% - and an admin read it as "the model
learned what I selected". It cannot mean that. It is measured on the data just fitted, it
averages every category together, and it describes the linear probe alone while the Articles
tab is decided by three channels (written phrasings, the probe, and admin exemplars) together.
An admin who selected the gym pictures, retrained, saw 85.4% and then watched a gym photograph
publish had no way to reach the facts that mattered: their category was at 98.6%, one picture
they had marked BLOCK still published, and one they had marked ALLOW was refused anyway.

So ImageTrainer.category_report scores each stored embedding through score_embedding and
decides_reject - the same functions, bars and channels the Articles tab uses. That is the
point of the split in VisualCheck, and the first test here is the one that keeps it honest:
scoring an embedding must reach the same verdict as scoring the picture it came from. A report
computed by a second implementation of the decision is how a dashboard ends up confidently
describing something the service does not do.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.clip import VisualCheck, VisualConfig  # noqa: E402
from app.image_trainer import ImageTrainer  # noqa: E402
from app.system_store import SystemStore  # noqa: E402

MODEL = ROOT / "models" / "img_v1"


def png(colour, size=(96, 96)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def visual():
    vc = VisualCheck(VisualConfig(enabled=True, model_dir=str(MODEL)))
    if not vc.ready:
        pytest.skip("the image model is not present in this checkout")
    vc.reload_exemplars()
    return vc


def embed_of(visual, colour):
    """The raw encoder output for a flat colour, as the trainer would have stored it."""
    from PIL import Image
    im = Image.open(io.BytesIO(png(colour))).convert("RGB")
    pixels = visual.preprocess(im)
    return visual._session.run([visual._image_output], {"pixel_values": pixels})[0]


# ---- the split must not change the answer ---------------------------------------------

def test_scoring_an_embedding_matches_scoring_the_picture(visual):
    """The guarantee the whole report rests on. If these ever diverge, every number the
    dashboard shows is about a decision the Articles tab does not make."""
    from PIL import Image
    for colour in ((190, 60, 60), (40, 90, 170), (230, 225, 215), (20, 20, 20)):
        im = Image.open(io.BytesIO(png(colour))).convert("RGB")
        direct = visual.classify(im)
        viaembed = visual.score_embedding(embed_of(visual, colour))

        assert direct.checked and viaembed.checked
        assert direct.label == viaembed.label
        assert direct.category == viaembed.category
        assert direct.score == pytest.approx(viaembed.score, abs=1e-5)
        assert direct.probe_unsafe == pytest.approx(viaembed.probe_unsafe, abs=1e-5)
        assert direct.exemplar_block == pytest.approx(viaembed.exemplar_block, abs=1e-5)
        assert visual.decides_reject(direct) == visual.decides_reject(viaembed)


def test_an_unnormalised_embedding_is_accepted(visual):
    """Embeddings come out of system.db as raw float32 and nothing guarantees they are unit
    length - an older row may predate the normalisation. Scaling one must not move the
    verdict, because cosine similarity does not care and a report that did would be
    reporting on the storage rather than on the pictures."""
    e = embed_of(visual, (140, 70, 180))
    a = visual.score_embedding(e / np.linalg.norm(e))
    b = visual.score_embedding(e * 7.5)
    assert a.label == b.label
    assert a.score == pytest.approx(b.score, abs=1e-5)


def test_a_flat_embedding_is_accepted_like_a_batch_of_one(visual):
    """system.db stores a bare (512,) vector; classify() works in (1, 512)."""
    e = embed_of(visual, (90, 150, 90))
    assert visual.score_embedding(e[0]).label == visual.score_embedding(e).label


# ---- the report ------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    return SystemStore(path=tmp_path / "rep.db", images_dir=tmp_path / "no-images")


def blob_of(visual, colour) -> bytes:
    e = embed_of(visual, colour)[0]
    return (e / np.linalg.norm(e)).astype(np.float32).tobytes()


def test_the_report_names_the_category_and_how_it_teaches(visual, store):
    """Per category: how many pictures, which way each is teaching, how many refuse now."""
    blob = blob_of(visual, (200, 120, 60))
    for i in range(3):
        store.add_image(filename=f"b{i}.png", file_path=f"x/b{i}.png", category="physique",
                        action="block", is_selected=1, embedding=blob)
    store.add_image(filename="a0.png", file_path="x/a0.png", category="physique",
                    action="allow", is_selected=1, embedding=blob)

    rep = ImageTrainer(store, visual_check=visual).category_report(
        store.get_selected_for_training())
    assert "physique" in rep
    r = rep["physique"]
    assert r["n"] == 4
    assert r["teaching_block"] == 3 and r["teaching_allow"] == 1
    assert 0 <= r["refused"] <= 4
    assert r["refused_pct"] == pytest.approx(100.0 * r["refused"] / r["n"], abs=0.1)


def test_a_category_teaching_both_ways_is_flagged(visual, store):
    """The thing that made "select all the gym images" not work: 49 of the 64 were ALLOW
    examples, so the selection pushed both ways at once and nothing said so."""
    blob = blob_of(visual, (120, 160, 200))
    store.add_image(filename="block.png", file_path="x/1.png", category="physique",
                    action="block", is_selected=1, embedding=blob)
    store.add_image(filename="allow.png", file_path="x/2.png", category="physique",
                    action="allow", is_selected=1, embedding=blob)
    store.add_image(filename="only.png", file_path="x/3.png", category="weapon",
                    action="block", is_selected=1, embedding=blob)

    rep = ImageTrainer(store, visual_check=visual).category_report(
        store.get_selected_for_training())
    assert rep["physique"]["teaches_both_ways"] is True
    assert rep["weapon"]["teaches_both_ways"] is False


def test_identical_pictures_marked_both_ways_always_contradict_one_label(visual, store):
    """Two rows holding the SAME embedding, marked allow and block. Whatever the model decides
    it must disagree with exactly one of them, so this pins contradicting to a real
    disagreement rather than to a count that happens to be non-zero."""
    blob = blob_of(visual, (60, 60, 60))
    store.add_image(filename="same_allow.png", file_path="x/1.png", category="physique",
                    action="allow", is_selected=1, embedding=blob)
    store.add_image(filename="same_block.png", file_path="x/2.png", category="physique",
                    action="block", is_selected=1, embedding=blob)

    r = ImageTrainer(store, visual_check=visual).category_report(
        store.get_selected_for_training())["physique"]
    assert r["contradicting"] == 1
    ex = r["examples"][0]
    assert ex["filename"] in ("same_allow.png", "same_block.png")
    # The row has to be actionable: which picture, which way it was marked, what happened,
    # and which channel did it. A count alone is what sent somebody to a shell to find out.
    assert ex["marked"] in ("allow", "block")
    assert ex["verdict"] in ("refused", "published")
    assert "top_label" in ex and "probe_unsafe" in ex


def test_rows_without_embeddings_are_skipped_not_counted(visual, store):
    """An image whose embedding could not be computed has no verdict, and counting it as
    published would report a refusal rate lower than the truth."""
    blob = blob_of(visual, (210, 210, 100))
    store.add_image(filename="has.png", file_path="x/1.png", category="weapon",
                    action="block", is_selected=1, embedding=blob)
    store.add_image(filename="none.png", file_path="x/2.png", category="weapon",
                    action="block", is_selected=1)

    r = ImageTrainer(store, visual_check=visual).category_report(
        store.get_selected_for_training())["weapon"]
    assert r["n"] == 1


def test_the_report_is_not_required_for_a_retrain_to_work(store):
    """It is a diagnostic. A diagnostic that can break the thing it describes is worse than
    none - the same rule the visual refusal attribution follows."""
    assert ImageTrainer(store, visual_check=None).category_report([]) == {}

    class Broken:
        ready = True
        config = None

        def score_embedding(self, embed):
            raise RuntimeError("no")

        def decides_reject(self, r):
            raise RuntimeError("no")

    store.add_image(filename="x.png", file_path="x/1.png", category="weapon", action="block",
                    is_selected=1, embedding=np.zeros(512, dtype=np.float32).tobytes())
    assert ImageTrainer(store, visual_check=Broken()).category_report(
        store.get_selected_for_training()) == {}


# ---- when a retrain moves the probe and the verdict does not move with it --------------

def record_previous(store, report):
    store.record_training(model_name="prev", action_type="fine_tune", status="completed",
                          total_samples=1, allowed_count=0, blocked_count=1,
                          classes=["safe", "weapon"], accuracy=0.9,
                          details={"category_report": report})


def test_a_change_the_verdict_absorbed_is_reported_as_such(store):
    """The sequence that made the control look one-way. Deselecting the gym pictures takes
    the probe from 45 refusals to 15 and the written rule picks up all 30, so the verdict
    stays at 71 of 72 and the screen used to show nothing at all."""
    record_previous(store, {"physique": {"n": 72, "refused": 71,
                                         "by_cause": {"policy": 26, "probe": 45}}})
    now = {"physique": {"n": 72, "refused": 71, "by_cause": {"policy": 56, "probe": 15},
                        "by_label": {"physique": 54, "revealing": 2}}}
    ImageTrainer(store, visual_check=None)._mark_masked_changes(now)

    r = now["physique"]
    assert r["probe_refused_before"] == 45 and r["probe_refused"] == 15
    assert r["masked_by"] == "policy"
    assert "did not change" in r["masked_note"]
    assert "30" in r["masked_note"]
    # And it names the rule, because "the policy channel" leaves the admin hunting for it
    # while "the physique rule" is the row to open in the policy tab.
    assert r["masked_by_label"] == "physique"
    assert "physique rule" in r["masked_note"]


def test_a_change_that_did_move_the_verdict_is_not_called_masked(store):
    """The note must mean something. If it appeared whenever the probe moved it would be
    noise on every retrain, and the one time it matters would read like all the others."""
    record_previous(store, {"weapon": {"n": 500, "refused": 400,
                                       "by_cause": {"probe": 300, "policy": 100}}})
    now = {"weapon": {"n": 500, "refused": 454, "by_cause": {"probe": 354, "policy": 100}}}
    ImageTrainer(store, visual_check=None)._mark_masked_changes(now)
    assert "masked_note" not in now["weapon"]
    assert now["weapon"]["probe_refused_before"] == 300


def test_an_unchanged_probe_says_nothing(store):
    """Retraining twice with no change in between must not invent a story about it."""
    rep = {"gore": {"n": 300, "refused": 299, "by_cause": {"probe": 250, "policy": 49}}}
    record_previous(store, rep)
    now = {"gore": dict(rep["gore"])}
    ImageTrainer(store, visual_check=None)._mark_masked_changes(now)
    assert "masked_note" not in now["gore"]


def test_the_first_ever_retrain_has_nothing_to_compare_against(store):
    """No history, no deltas, no crash - and no invented baseline of zero, which would
    report every category as having changed on the first run."""
    now = {"physique": {"n": 72, "refused": 71, "by_cause": {"probe": 45}}}
    ImageTrainer(store, visual_check=None)._mark_masked_changes(now)
    assert "probe_refused_before" not in now["physique"]
    assert "masked_note" not in now["physique"]


def test_a_category_that_is_new_since_the_last_retrain_is_skipped(store):
    """An admin adds a category between runs. It has no previous numbers, so it gets no
    delta rather than a delta measured against nothing."""
    record_previous(store, {"weapon": {"n": 5, "refused": 5, "by_cause": {"probe": 5}}})
    now = {"weapon": {"n": 5, "refused": 5, "by_cause": {"probe": 5}},
           "rainbow": {"n": 2, "refused": 2, "by_cause": {"exemplar": 2}}}
    ImageTrainer(store, visual_check=None)._mark_masked_changes(now)
    assert "probe_refused_before" not in now["rainbow"]
