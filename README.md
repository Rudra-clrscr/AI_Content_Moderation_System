# AI Content Moderation System — BONC Network

Screens business profiles, product listings, posts, articles and ads **before they go live** —
the words and the pictures. There is no moderator queue: content that breaks policy is refused,
and the author is shown what to change.

| | |
|---|---|
| **Text** | DeBERTa-v3-small, ONNX (`models/v6`) + a regex gate + a linear triage filter |
| **Pictures** | CLIP ViT-B/32 zero-shot against a written policy (`models/img_v1`) + a 5-class probe |
| **Serving** | Flask + ONNX Runtime, SQL Server for the audit trail, Celery/Redis for async |
| **Tests** | 450, `python -m pytest` from `flask_api/` |

---

## Quick start

```bash
git clone <repo> && cd BONC
git lfs pull                                  # the text model is tracked via LFS

cd flask_api
python -m venv .venv && .venv/Scripts/activate # Windows; use bin/activate elsewhere
pip install -r requirements.txt

python scripts/fetch_clip.py                   # 335 MB image encoder, NOT in git
python scripts/build_clip_prompts.py --check   # must say "matches visual_policy.yaml"

python wsgi.py
```

**Do not skip the two `fetch_clip` lines.** Without them the service still starts and still
answers `200` — it just never looks at pictures, and every result says
`"visual_content_checked": false`. Nothing fails loudly. Check with:

```bash
curl http://localhost:8000/ready        # look for "visual": {"ready": true}
```

Full walkthroughs: [SETUP_IMAGE_MODEL.md](flask_api/SETUP_IMAGE_MODEL.md) ·
[PowerShell](flask_api/DEMO_GUIDE_POWERSHELL.md) · [CMD](flask_api/DEMO_GUIDE_CMD.md) ·
[demo script](flask_api/DEMO_GUIDE.md)

---

## The API

| endpoint | what it takes |
|---|---|
| `POST /v1/moderate` | `{content, content_type, content_id}` — text |
| `POST /v1/moderate/media` | multipart file — one image, PDF or video per request |
| `POST /v1/moderate/pdf` | multipart PDF — extraction plus OCR for scans |
| `GET /ready` | model versions, thresholds, **whether pictures are actually being checked** |
| `GET /health` | liveness |
| `POST /v1/admin/model/reload` | hot-swap the text model |

`content_type` is the surface — `product_listing`, `article`, `post`, `business_profile`,
`advertisement`, `video`, `request`, `proposal`, `business_proposal`. It doesn't change the
score; it selects the wording the author sees and is stored with the decision.

The response shape is specified in [contracts/moderation_result.md](contracts/moderation_result.md).
A reference front-end client lives in
[flask_api/app/static/articles/moderation-client.js](flask_api/app/static/articles/moderation-client.js)
— framework-free, covers every surface, and is the single implementation of *nothing publishes
until the service allows*.

---

## Changing the policy

This is the most common task, and both policies are plain files you can review in a pull request.

### Words — [`config/gate_patterns.yaml`](flask_api/config/gate_patterns.yaml)

Regex and term lists that can `block`, `revise` or `flag` on the spot, before the model runs.
Author-facing wording is in [`config/feedback_messages.yaml`](flask_api/config/feedback_messages.yaml).

### Pictures — [`config/visual_policy.yaml`](flask_api/config/visual_policy.yaml)

11 labels, each a handful of English phrasings and its own threshold:

| label | category | bar | label | category | bar |
|---|---|---|---|---|---|
| `wedding` | wedding_content | 0.50 | `romantic` | romantic_content | 0.75 |
| `physique` | physique_display | 0.55 | `sexual` | sexual_content | 0.80 |
| `manhandling` | violence_or_unrest | 0.60 | `revealing` | revealing_clothing | 0.80 |
| `unrest` | violence_or_unrest | 0.70 | `minor` | minors | 0.85 |
| `weapon` / `gore` | weapons / graphic_violence | 0.90 | `safe` | — | 82 prompts |

After editing it:

```bash
python scripts/fetch_clip.py --with-text-encoder     # once; needed only to rebuild
python scripts/build_clip_prompts.py
python scripts/eval_clip_images.py --images ../images --device cuda
```

Commit the rebuilt `prompts.npz` and `model_meta.json` with the YAML. `--check` fails if they
drift apart.

**Two rules this file has learned the hard way:**

1. **Name the scene, not the pose.** *"a person exercising"* also describes someone aiming a
   rifle. *"a person lifting weights in a gym"* does not. This mistake has cost recall five
   separate times and each instance is commented in the file.
2. **The safe list is not decoration.** It is what the unsafe labels are judged against — a
   picture matching nothing in it has nothing to be safe *as*, and its unsafe score rises
   whatever is really in it. A test fails if the safe prompts fall into the minority.

---

## Measuring before you ship

```bash
python scripts/eval_clip_images.py --images ../images --device cuda
```

Prints every set, then the **shipped decision** — per-label bars, the combined unsafe score and
the probe, exactly as the service decides. `--probe-reject-min` and `--reject-min` let you sweep.

**Read [docs/image_sets.md](docs/image_sets.md) before quoting any number from it.** The sets are
not equally trustworthy, and two of them measure nothing:

| set | genuinely what it claims |
|---|---|
| `weddings`, `gym_neg`, `unrest_real`, `nudity` — **supplied** | representative |
| `revealing`, `minors` | ~79% |
| `romance` | ~69% |
| `unrest` | ~67% |
| `violence`, `threats` | **~17% / ~12% — reported but excluded** |

Collected sets average **68%** recall; supplied ones **99%**. If a number looks weak, check
which kind of set produced it before changing the model.

Retraining the image probe (rarely needed):

```bash
pip install -r training/requirements.txt       # scikit-learn only, for this script
python training/train_image_probe.py --device cuda
```

Defaults to the five classes that ship. `--classes` tries others; adding more has been measured
twice and made things worse — see the comment block in that file before trying a third time.

---

## Repo map

| path | |
|---|---|
| [flask_api/](flask_api/) | the service, configs, scripts, 450 tests |
| [training/](training/) | text model pipeline + the image probe trainer |
| [contracts/](contracts/) | the result shape and the model bundle spec — read first |
| [docs/](docs/) | set provenance, measurement notes, improvement guide |
| `images/` | evaluation sets. **gitignored** — not in the repo |

---

## Contributing

- **Work on a feature branch and merge through a pull request.** Never commit to `main`.
- **Measure before you open it.** Any change to a policy file or a model needs the eval output
  in the PR description. A wording that reads well can cost a label half its recall.
- **Quote numbers with their set.** "47% on violence" means little when that folder is ~17%
  violence. Say which set, and whether it was supplied or collected.
- **Don't let an artifact drift from its trainer.** `probe.npz` and `CLASSES` disagreed for
  weeks because a file was written and the code edited back a minute later without a re-run.
  If you retrain, commit the artifact in the same change.
- **Leave the "post anyway" override out.** Content the moderator objects to is not published;
  the only way on is to change it. A test enforces this.

---

## Known limits

- **`sexual` has 37 images behind it, all one genre.** It scored a literal 0.0% as a probe
  class. Treat its recall as thin.
- **`manhandling` has no training data at all** — its only sources were the two audited sets.
  It is carried by its 14 prompts, which do catch 99% of real press photography of confrontation.
- **`minors_incidental` sits at ~29% false refusals.** That is the deliberate price of refusing
  pictures *of* children at 97%; a graduation photo can be caught.
- **Video is not watched.** Only its text is read, and the result says so. Video speech transcription and subtitles extraction are supported via the offline pipeline in [docs/video_transcription_pipeline.md](docs/video_transcription_pipeline.md).
- **`images/_quarantine_explicit/`** holds 18 files pulled out of the children's folder during
  the audit. They need a human decision — see [docs/image_sets.md](docs/image_sets.md).
