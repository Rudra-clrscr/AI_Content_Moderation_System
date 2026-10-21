"""Encode phrasings into embeddings, for previewing a policy rule before it is applied.

    echo '["a photo of fireworks"]' | python scripts/encode_prompts.py --model-dir models/img_v1

Reads a JSON list of strings on stdin, writes {"embeds": [[...], ...], "dim": n} on stdout.

WHY A SEPARATE PROCESS, AND WHY SO SMALL. The text encoder is 254 MB and app/clip.py
deliberately never loads it - the serving process carries the vision tower and prompts.npz and
nothing else. build_clip_prompts.py honours that by running offline, and the policy preview
needs exactly the same thing for a handful of candidate phrasings, so it honours it the same
way: the admin API shells out, the encoder lives and dies here, and the web process stays as
small as it was.

Encoding is a copy of the eleven lines in build_clip_prompts.py rather than an import of them,
and that is a deliberate trade. Those lines sit inside its main() between reading the policy
and writing the npz, so importing would mean refactoring the script every serving path depends
on. The duplication is bounded (tokenise, pad, run, L2-normalise) and it is pinned by
test_policy_preview.py, which fails if a prompt encoded here stops matching the same prompt
encoded there - the one way this could drift and silently score a preview against a different
vector space than the live policy.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def encode(prompts: list[str], model_dir: Path):
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    text_model = model_dir / "text_model.onnx"
    tokenizer = model_dir / "tokenizer.json"
    for p in (text_model, tokenizer):
        if not p.exists():
            raise SystemExit(f"missing {p} - run `python scripts/fetch_clip.py "
                             f"--with-text-encoder` first")

    tok = Tokenizer.from_file(str(tokenizer))
    encoded = [tok.encode(t).ids for t in prompts]
    width = max(len(e) for e in encoded)
    ids = np.zeros((len(encoded), width), dtype=np.int64)
    for i, e in enumerate(encoded):
        ids[i, : len(e)] = e

    sess = ort.InferenceSession(str(text_model), providers=["CPUExecutionProvider"])
    out_names = [o.name for o in sess.get_outputs()]
    want = "text_embeds" if "text_embeds" in out_names else "pooler_output"
    embeds = sess.run([want], {"input_ids": ids})[0].astype(np.float32)
    embeds /= np.maximum(np.linalg.norm(embeds, axis=1, keepdims=True), 1e-12)
    return embeds


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-dir", type=Path,
                    default=Path(__file__).resolve().parent.parent / "models" / "img_v1")
    args = ap.parse_args()
    prompts = json.load(sys.stdin)
    if not isinstance(prompts, list) or not prompts:
        print(json.dumps({"error": "expected a non-empty JSON list of strings"}))
        return 2
    embeds = encode([str(p) for p in prompts], args.model_dir)
    json.dump({"embeds": embeds.tolist(), "dim": int(embeds.shape[1])}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
