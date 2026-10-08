"""Turn config/visual_policy.yaml into the prompt embeddings the visual check scores against.

    python scripts/build_clip_prompts.py              # after editing the policy file

Run this once offline, and again whenever the policy file changes. It writes
`models/img_v1/prompts.npz`: one L2-normalised 512-float vector per phrasing, the label each
belongs to, and which labels are unsafe.

Doing it here rather than at startup is what keeps the text encoder (254 MB) and the CLIP
tokenizer out of the serving image altogether - app/clip.py loads the vision encoder and this
file, nothing else. It also means the policy is frozen at a known point: the npz records the
hash of the policy file it was built from, so a stale one is caught by
`--check` instead of being quietly scored against.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The image model is its own bundle with its own version, separate from the text moderation
# model in models/v5. They are different models doing different jobs and they version apart.
BUNDLE_VERSION = "clip-vit-b32-bonc-img-v1"
BASE_MODEL = "Xenova/clip-vit-base-patch32"


def policy_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def load_policy(path: Path) -> tuple[list[str], list[str], dict[str, str]]:
    """(prompts, label_per_prompt, {unsafe label: policy category})."""
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    labels = raw.get("labels") or {}
    if not labels:
        raise SystemExit(f"{path} has no `labels`")

    prompts, per_prompt, categories = [], [], {}
    for name, spec in labels.items():
        texts = [str(t).strip() for t in (spec.get("prompts") or []) if str(t).strip()]
        if not texts:
            raise SystemExit(f"label {name!r} has no prompts")
        if spec.get("unsafe"):
            category = str(spec.get("category") or name)
            categories[str(name)] = category
        for t in texts:
            prompts.append(t)
            per_prompt.append(str(name))
    if not categories:
        raise SystemExit(f"{path} marks no label `unsafe: true`, so nothing would ever be flagged")
    safe = [n for n in labels if not (labels[n] or {}).get("unsafe")]
    if not safe:
        raise SystemExit(f"{path} has no safe labels - the unsafe ones would have nothing to be "
                         "judged against and every picture would look unsafe")
    return prompts, per_prompt, categories


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--policy", type=Path, default=ROOT / "config" / "visual_policy.yaml")
    ap.add_argument("--model-dir", type=Path, default=ROOT / "models" / "img_v1")
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the existing prompts.npz is stale, and write nothing")
    args = ap.parse_args()

    if not args.policy.exists():
        print(f"no policy file at {args.policy}", file=sys.stderr)
        return 2
    digest = policy_digest(args.policy)
    out = args.model_dir / "prompts.npz"

    if args.check:
        if not out.exists():
            print(f"{out} does not exist", file=sys.stderr)
            return 1
        import numpy as np
        have = str(np.load(out, allow_pickle=False).get("policy_digest", ""))
        if have != digest:
            print(f"{out} was built from a different {args.policy.name} "
                  f"({have or 'unstamped'} != {digest}) - re-run this script", file=sys.stderr)
            return 1
        print(f"{out} matches {args.policy.name} ({digest})")
        return 0

    prompts, per_prompt, categories = load_policy(args.policy)
    text_model = args.model_dir / "text_model.onnx"
    tokenizer = args.model_dir / "tokenizer.json"
    for p in (text_model, tokenizer):
        if not p.exists():
            print(f"missing {p} - run `python scripts/fetch_clip.py --with-text-encoder` first",
                  file=sys.stderr)
            return 2

    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(tokenizer))
    encoded = [tok.encode(t).ids for t in prompts]
    width = max(len(e) for e in encoded)
    ids = np.zeros((len(encoded), width), dtype=np.int64)
    for i, e in enumerate(encoded):
        ids[i, : len(e)] = e

    sess = ort.InferenceSession(str(text_model), providers=["CPUExecutionProvider"])
    embeds = sess.run(None, {"input_ids": ids})[0].astype(np.float32)
    embeds /= np.maximum(np.linalg.norm(embeds, axis=1, keepdims=True), 1e-12)

    args.model_dir.mkdir(parents=True, exist_ok=True)
    by_label: dict[str, int] = {}
    for name in per_prompt:
        by_label[name] = by_label.get(name, 0) + 1

    # The bundle describes itself, like models/v5/model_meta.json does for the text model.
    # These are two separate models with separate versions: the text one scores words, this one
    # scores pictures, and neither is a fallback for the other.
    meta = {
        "version": BUNDLE_VERSION,
        "kind": "image",
        "task": "zero-shot image classification against config/visual_policy.yaml",
        "base_model": BASE_MODEL,
        "architecture": "CLIP ViT-B/32, vision encoder only (512-d projected embeddings)",
        "image_size": 224,
        "serving_files": ["vision_model.onnx", "prompts.npz"],
        "policy_file": args.policy.name,
        "policy_digest": digest,
        "labels": {n: ("unsafe: " + categories[n]) if n in categories else "safe"
                   for n in sorted(by_label)},
        "prompt_counts": dict(sorted(by_label.items())),
        "recommended_thresholds": {"reject_min": 0.90},
        "note": ("The text encoder is NOT needed to serve and is not part of the serving set - "
                 "this script runs it offline to bake prompts.npz. Re-run after any change to "
                 "the policy file; app/clip.py scores against whatever is in prompts.npz."),
    }
    (args.model_dir / "model_meta.json").write_text(
        json.dumps(meta, indent=2) + chr(10), encoding="utf-8")

    np.savez(out,
             embeds=embeds,
             labels=np.array(per_prompt),
             prompts=np.array(prompts),
             category_labels=np.array(list(categories)),
             categories=np.array(list(categories.values())),
             policy_digest=np.array(digest))

    print(f"{out}  ({out.stat().st_size / 1024:.0f} KB)")
    print(f"  {BUNDLE_VERSION}: {len(prompts)} prompts, {embeds.shape[1]}-d, "
          f"from {args.policy.name} ({digest})")
    for name, count in sorted(by_label.items()):
        mark = f"unsafe -> {categories[name]}" if name in categories else "safe"
        print(f"    {name:10} {count:3} prompts   {mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
