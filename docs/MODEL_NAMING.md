# Model name: BONC Modra AI – Content Compliance Model (CCM)

**BONC Modra AI – Content Compliance Model (CCM)** is the name of this service's moderation
model, for both the text and the image side. Short form: **BONC Modra AI**. Use it in docs,
demos, UI text and code. Don't use "the DeBERTa model" or "the CLIP check".

The name is a brand over two open models that BONC fine-tuned and configured. This page
keeps the record of what each one is built on.

| Part | Built on | Where it lives |
|---|---|---|
| Text model | **DeBERTa-v3-small** (`microsoft/deberta-v3-small`), fine-tuned on BONC data, exported to ONNX | `flask_api/app/model.py`, bundle in `flask_api/models/<version>/`, training in `training/` |
| Image model | **CLIP ViT-B/32** (`openai/clip-vit-base-patch32`, ONNX export `Xenova/clip-vit-base-patch32`), scored against `config/visual_policy.yaml`, plus a probe trained on BONC images | `flask_api/app/modra_image.py`, bundle in `flask_api/models/img_v1/` |

## Renamed files (2026-11-28)

| Before | After |
|---|---|
| `flask_api/app/clip.py` | `flask_api/app/modra_image.py` |
| `flask_api/scripts/fetch_clip.py` | `flask_api/scripts/fetch_modra_image.py` |
| `flask_api/scripts/build_clip_prompts.py` | `flask_api/scripts/build_modra_prompts.py` |
| `flask_api/scripts/eval_clip_images.py` | `flask_api/scripts/eval_modra_images.py` |

## What deliberately still says DeBERTa or CLIP

These name the upstream models, not ours. Renaming them would break something or make them
false:

- **The API.** No endpoint, field or returned value changed. `model_version` is still the
  bundle's version string (for example `bonc-v9-ver3`), and the health check names are
  unchanged.
- **Download addresses and checksums.** These are the Hugging Face repo IDs and the pinned
  SHA256 values in `fetch_modra_image.py`, and `BASE = "microsoft/deberta-v3-small"` in
  `training/train.py`.
- **Version strings inside model files**, such as `clip-vit-b32-bonc-img-v1` in
  `models/img_v1/model_meta.json` and the probe. These are checked on load, so a probe never
  runs on the wrong encoder. Older text bundles are named `deberta-v3-small-bonc-v*`, and
  `training/accept_model.py` parses that prefix.
- **The `family` setting (`clip` / `siglip2`).** It selects the preprocessing and scoring
  rules of the base architecture, which really do differ between the two.
- **Technical notes about the base models.** These include CLIP-vs-SigLIP preprocessing
  constants, the DeBERTa tokenizer, a `transformers` 5.x bug and the licence note in
  `fetch_modra_image.py`.
- **Older reports in `docs/`.** They describe measurements made under the old names and are
  kept as written. File paths in them were updated.
