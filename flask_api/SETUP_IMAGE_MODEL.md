# Setting up the image model

The service screens **pictures** as well as text: a weapon in a product photo, a wedding album,
a gym selfie, a child as the subject. That is a separate model from the text one, it lives in
`models/img_v1/`, and **two of its files are not in git**.

If you skip this, the service still starts and still answers 200. It just does not look at
pictures, and every result says `"visual_content_checked": false`. Nothing breaks loudly — so
it is worth doing deliberately rather than discovering later.

## Two commands

From `flask_api/`, with the virtualenv active:

```
python scripts/fetch_clip.py          # 335 MB, SHA256-verified against a pinned revision
python scripts/build_clip_prompts.py --check
```

The first downloads the CLIP vision encoder. The second should print
`prompts.npz matches visual_policy.yaml`. If it does, you are done.

## Why those two are not in the repo

| file | in git? | why |
|---|---|---|
| `prompts.npz` | **yes**, 372 KB | the policy, encoded. Ships so a serving host needs no text tower |
| `probe.npz` | **yes**, 9 KB | fitted on 968 of BONC's own labelled pictures. Not regenerable from anything public |
| `model_meta.json` | **yes** | which encoder the bundle expects, and the policy digest |
| `vision_model.onnx` | no, 335 MB | third-party weights, licence not declared by the publisher |
| `text_model.onnx` | no, 242 MB | same, and only needed to *edit* the policy |

577 MB of weights in Git LFS would also exhaust a free-tier quota by itself, and then every
clone fails on the data limit rather than on anything we wrote. `fetch_clip.py` pins a specific
revision and checks SHA256 before moving anything into place, so everyone gets byte-identical
weights without the repo carrying them.

## Check it is actually on

```
curl http://localhost:8000/ready
```

Look for `"visual": {"ready": true, ...}`. **That is the field to alert on in deployment** — it
is how you find out the picture check is off before a member does.

## If you edit the policy

`config/visual_policy.yaml` is the policy — labels, prompts, per-label thresholds. It is plain
YAML and it is tracked, so changes are reviewable in a pull request. After editing it:

```
python scripts/fetch_clip.py --with-text-encoder     # once; 242 MB, only needed to rebuild
python scripts/build_clip_prompts.py
python scripts/eval_clip_images.py --images ../images --device cuda
```

The last one prints what the change did to every image set. **Run it before opening the PR** —
a wording that reads well can cost a whole label's recall, and this file has a long record of
exactly that happening. `docs/image_sets.md` says what each set can honestly measure.

Commit the rebuilt `prompts.npz` and `model_meta.json` along with the YAML; `--check` fails in
CI if the three drift apart.

## The optional referee

`scripts/fetch_nsfw_reference.py` fetches a second classifier used by
`scripts/eval_sexual_label.py` and by the pixel gate in `scripts/collect_commons_images.py`.
Nothing in `app/` loads it and the service runs identically without it. You need it only if you
are measuring the `sexual` label or collecting new image sets.
