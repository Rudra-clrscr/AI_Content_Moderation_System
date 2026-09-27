# flask_api: moderation service

This service wraps the DeBERTa-v3-small ONNX model in a Flask API. A request goes
through three steps:

1. **Layer 1 gate.** Regex and term-list rules in [config/gate_patterns.yaml](config/gate_patterns.yaml) can reject content on the spot (`block`), send it back to the author with the problem highlighted (`revise`), or just note it for the audit log (`flag`).
2. **Model.** An ONNX Runtime session is loaded once at startup. It can be hot-swapped through `POST /v1/admin/model/reload`.
3. **Threshold routing.** The risk score becomes `allow`, `revise` or `reject`, using the thresholds in [config/settings.yaml](config/settings.yaml).

## Revise instead of human review

There's no moderator queue. Content in the middle band, or content that matches
a `revise` rule, goes back to its author, the way LinkedIn checks a post before
publishing it:

- **Rule matches** are highlighted exactly, using character offsets into the
  author's original text, even through tricks like full-width letters or extra
  spaces. Each one comes with the rule's instruction.
- **Model concerns:** each sentence is scored on its own (up to
  `max_highlight_sentences`, default 5), and the doubtful ones are highlighted,
  so the author knows which sentence to rephrase.
- **Rejects** name the policy area ("fraud and scams") but don't highlight the
  trigger words, so bad actors can't learn to evade the filters. Set
  `highlight_on_reject: true` to change this.

All wording lives in [config/feedback_messages.yaml](config/feedback_messages.yaml).
Allowed posts cost one inference (about 12 ms). A revise decision also scores
each sentence to find what to highlight (about 45 ms for a 3-sentence post).

The interfaces with the other two tracks are documented in [../contracts/](../contracts/).

## Quick start

```bash
cd flask_api
python -m venv .venv && .venv/Scripts/activate      # Windows; on Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q

# The live model is committed under models/v3 (previous model: models/v1) via Git LFS (install from https://git-lfs.com,
# then `git lfs install` once). If you cloned before installing LFS, run `git lfs pull`.
python wsgi.py

# To update to the ML team's latest model (github.com/Gupta35251/BONC), then commit it:
python scripts/fetch_model.py

# Or without a real model (dev only):
MODEL_BACKEND=stub python wsgi.py

curl -X POST localhost:8000/v1/moderate -H "Content-Type: application/json" \
  -d '{"content":"Bulk cotton yarn supplier","content_type":"product_listing","content_id":"LST-1"}'
```

For a live demonstration, set up with [DEMO_GUIDE_POWERSHELL.md](DEMO_GUIDE_POWERSHELL.md) or [DEMO_GUIDE_CMD.md](DEMO_GUIDE_CMD.md), then follow [DEMO_GUIDE.md](DEMO_GUIDE.md) for the run of show and tested sentences.

## Articles: publish-time moderation (front-end reference)

`/articles` (with `DEMO_PAGE=1`) is a working reference for the platform's
Articles tab and "Write article" modal, in BONC Business Dashboard styling:

- **Publish** sends title, body and link URLs to `/v1/moderate`
  (`content_type: "article"`):
  - **allow**: the article is published and uses one of the free publishes.
  - **revise**: the modal stays open, the problem parts are highlighted in the
    editor (CSS Custom Highlight API, so the text itself isn't changed), and each
    issue has a **Show** button. The article is kept under *Needs changes*.
  - **reject**: the policy area is shown, and the article is kept under *Rejected*.
  - If the check fails (network, 503), nothing is published (fail closed).
- **Save as Draft** skips moderation, because drafts aren't public.
- Only successful publishes use up the free-publish quota.
- `app/static/articles/moderation-client.js` is framework-free and can be copied
  into the platform unchanged. It turns the rich-text editor into moderation text
  (with link URLs) and maps the feedback offsets back onto the title, body and links.

Articles are stored in the browser (localStorage), because this is a front-end
reference, not the platform's article service.

A browser test covers every flow: `pip install playwright`, start the server with
`DEMO_PAGE=1`, then run `python scripts/e2e_articles.py --url http://127.0.0.1:8000`.
It uses your installed Chrome and runs 26 checks.

## Sentence scan

A scammer can hide one harmful sentence inside a normal listing, and the whole
text then scores as clean. For example, "Solar panels 330W mono PERC, 25 year
warranty. Limited slots for dealers, register now with a small fee." scores
0.000 as a whole. So every sentence of a multi-sentence post is also scored on
its own (up to `sentence_scan.max_sentences`, default 400, enough for a full
article):

- **Any sentence at reject level rejects the post** (`decided_by: "sentence"`).
- **A middle-band sentence in an otherwise allowed post asks for a revision**,
  with that sentence highlighted (`revise_on_middle`). This only works with
  calibrated `label_weights` (see below), because otherwise ordinary sentences
  land in the middle band.

The cost is one inference per sentence, about 10 ms each: a 40-sentence
article takes about 0.5 s. Sentences are scored one at a time, because batching
them changes the INT8 model's scores (dynamic quantization scales across the
whole batch; we measured differences up to 0.63). Grouping sentences into
larger windows was also tried for long articles, and dropped: a buried scam
sentence gets diluted inside the window. Turn the scan off with `SENTENCE_SCAN=0`.

## Score calibration (`label_weights`)

`risk = weight(safe) × P(safe) + weight(review) × P(review) + weight(reject) × P(reject)`.
v3's "review" class fires on almost any prose that isn't a product listing:
P(review) is about 0.999 for ordinary articles, titles and reviews, while
P(reject) stays at or below 0.015 on legitimate text. So its weight is calibrated
down to **0.25**, and "unsure" text scores about 0.25 and is allowed. Measured with
`scripts/compare_models.py models/v3 models/v3 --old-review-weight 0.5 --new-review-weight 0.25`:

| review weight | legit allowed | harmful caught | defects | sigma level | McNemar p |
|---|---|---|---|---|---|
| 0.50 | 4/36 | 17/18 | 33/54 | 1.22 | |
| **0.25** | **36/36** | 12/18 | **6/54** | **2.72** | < 0.0001 |

Re-measure for every new model. See `docs/MODEL_IMPROVEMENT_GUIDE.md`.

## Abuse aimed at someone

Two checks stop comments that attack another person or business:

- **Gate rule `abuse.targeted`** (instant, no model): a subject word (he, she,
  his, her, him, they, them, their) followed later in the same sentence by an
  insult or profanity is rejected.
- **Sentence check** ([app/targeted.py](app/targeted.py)): for each sentence that
  mentions someone, the model scores the text from the subject word to the end
  of the sentence. If that scores at reject level, the whole post is rejected
  (`decided_by: "targeted"`). This catches a threat hidden inside an otherwise
  friendly comment. It's configured under `targeted_abuse` in `settings.yaml`,
  and each request runs at most `max_segments` extra inferences.

The profanity lists aren't committed. Download them with
`python scripts/fetch_wordlists.py`, which pins each list to a commit and checks
its checksum. Entries with innocent B2B meanings are removed by
`config/wordlist_exclusions.txt`.

Before switching to a new model, compare it with the current one:
`python scripts/compare_models.py models/v1 models/v3` runs a fixed set of legitimate and harmful
texts through the full pipeline with each model and lists every decision that changes.

After changing the model, thresholds or rules, run
`python scripts/verify_demo_sentences.py` against a running server. It checks
every demo sentence against the decision the demo guide promises.

## Endpoints

| Method | Path | |
|---|---|---|
| POST | `/v1/moderate` | Moderates one piece of content. Returns `200` with the result, or `202` pending in async mode. |
| GET | `/v1/moderate/<request_id>` | Looks up a result (async mode only). |
| GET | `/demo` | Browser demo UI. Only served when `DEMO_PAGE=1`. |
| GET | `/health` | Liveness check. |
| GET | `/ready` | Readiness check. Returns `503` if no model is loaded in sync mode. |
| POST | `/v1/admin/model/reload` | Hot-swaps the model. Needs the `X-Admin-Token` header and the `ADMIN_TOKEN` env var. |

## Layout

```
app/
  gate.py       Layer 1 regex gate (text normalisation + rule compilation)
  model.py      ONNX session + tokenizer, ModelRegistry (atomic hot-swap), StubScorer
  routing.py    Thresholds + route()
  pipeline.py   gate -> model -> routing; builds the result payload
  sinks.py      ResultSink: where the database writer plugs in
  tasks.py      Celery task for async mode
  api.py        Flask endpoints
  config.py     settings.yaml + env overrides
config/         settings.yaml, gate_patterns.yaml (private term lists go in config/private/, gitignored)
scripts/        fetch_model.py, bench_latency.py, make_dummy_model.py
tests/
```

## Configuration

Everything lives in `config/settings.yaml`. These env vars override it:
`MODERATION_MODE`, `MODEL_BACKEND`, `MODEL_DIR`, `THRESHOLD_ALLOW_MAX`,
`THRESHOLD_REJECT_MIN`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` and `ADMIN_TOKEN`.

**Sync vs. async.** Set `mode: async` to have Flask run only the gate inline and queue
model inference to Celery. The worker is started with:

```
celery -A app.tasks:celery_app worker -Q moderation --pool=solo   # --pool=prefork on Linux
```

## Latency

```
python scripts/bench_latency.py --n 500
```

This measures the gate and the model separately, in-process. It exits non-zero when
p95 inference is over `latency_budget_ms` (30 ms). With the dummy model, the gate
p95 is about 0.1 ms. Real numbers need the INT8 DeBERTa export.
