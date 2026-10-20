"""The policy preview must encode phrasings the same way the live policy does.

scripts/encode_prompts.py duplicates eleven lines from scripts/build_clip_prompts.py -
tokenise, pad, run the text encoder, L2-normalise - because those lines sit inside the
builder's main() between reading the policy file and writing the npz, and importing them would
mean refactoring the script every serving path depends on.

Bounded duplication is a fair trade only while something notices if the two drift apart. This
is that something. If they ever disagree, the preview ranks pictures in a different vector
space from the one the live check scores against, and an admin would be choosing wordings on
evidence that does not apply - silently, because both halves would still look perfectly
healthy on their own.

The test re-encodes a phrasing that is already in prompts.npz and compares it to the vector
baked there. Same wording, same encoder, same normalisation: the vectors should be identical
to floating-point noise.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "models" / "img_v1"
PROMPTS = MODEL_DIR / "prompts.npz"
ENCODER = MODEL_DIR / "text_model.onnx"
SCRIPT = ROOT / "scripts" / "encode_prompts.py"

pytestmark = pytest.mark.skipif(
    not (PROMPTS.exists() and ENCODER.exists() and SCRIPT.exists()),
    reason="needs models/img_v1 with the 254 MB text encoder (scripts/fetch_clip.py "
           "--with-text-encoder); it is not in git",
)


def test_preview_encodes_identically_to_the_baked_policy():
    import numpy as np

    data = np.load(PROMPTS, allow_pickle=False)
    prompts = [str(x) for x in data["prompts"]]
    baked = data["embeds"].astype(np.float32)
    assert prompts, "prompts.npz carries no phrasings"

    # A handful from across the file rather than the first few, which all belong to one label.
    picks = sorted({0, len(prompts) // 3, len(prompts) // 2, len(prompts) - 1})
    sample = [prompts[i] for i in picks]

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--model-dir", str(MODEL_DIR)],
        input=json.dumps(sample), capture_output=True, text=True, timeout=600, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stderr[-800:]
    got = np.asarray(json.loads(proc.stdout)["embeds"], dtype=np.float32)

    assert got.shape == (len(sample), baked.shape[1])
    for n, (i, row) in enumerate(zip(picks, got)):
        # Cosine against the baked vector for the same phrasing. Both are L2-normalised, so
        # this is a dot product and anything below 0.999 means the two encoders have diverged.
        cos = float(row @ baked[i])
        assert cos > 0.999, (
            f"phrasing {sample[n]!r} encodes differently in scripts/encode_prompts.py than it "
            f"does in scripts/build_clip_prompts.py (cosine {cos:.5f}). The policy preview "
            f"would rank pictures in a different vector space from the live check. Re-sync "
            f"the encoding block in encode_prompts.py with the one in build_clip_prompts.py.")


def test_preview_output_is_normalised():
    """A preview score is a cosine, which is only a cosine if both sides are unit length."""
    import numpy as np

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--model-dir", str(MODEL_DIR)],
        input=json.dumps(["a photo of a wedding ceremony", "a photo of a handgun"]),
        capture_output=True, text=True, timeout=600, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stderr[-800:]
    got = np.asarray(json.loads(proc.stdout)["embeds"], dtype=np.float32)
    norms = np.linalg.norm(got, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-4), f"not unit vectors: {norms}"
