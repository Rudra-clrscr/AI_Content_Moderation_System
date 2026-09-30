# flask_api: moderation service

This service wraps the DeBERTa-v3-small ONNX model in a Flask API. A request goes
through three steps:

1. **Layer 1 gate.** Regex and term-list rules in [config/gate_patterns.yaml](config/gate_patterns.yaml) can reject content on the spot (`block`), send it back to the author with the problem highlighted (`revise`), or just note it for the audit log (`flag`).
2. **Model.** An ONNX Runtime session is loaded once at startup. It can be hot-swapped through `POST /v1/admin/model/reload`.
3. **Threshold routing.** The risk score becomes a decision, using the thresholds in [config/settings.yaml](config/settings.yaml). With v4 there is one boundary: **risk ≤ 0.5 is allowed, risk > 0.5 is rejected**. The risk is the model's calibrated P(unsafe).

## Rewrite instead of human review

There's no moderator queue. Rejected content, or content that matches a `revise`
rule, goes back to its author, the way LinkedIn checks a post before publishing it:

- **Rule matches** are highlighted exactly, using character offsets into the
  author's original text, even through tricks like full-width letters or extra
  spaces. Each one comes with the rule's instruction.
- **Model concerns:**
  - each sentence is scored on its own, and the sentences over the boundary are highlighted;
  - inside each highlighted sentence, the **trigger words** (word-by-word scan, below) are marked, so the author knows exactly what to rewrite.
- **Rejects** name the policy area and highlight what to rewrite
  (`highlight_on_reject: true`), because with v4 a rejected author is expected to
  rewrite and resubmit. Set it to `false` to show only the policy area.

All wording lives in [config/feedback_messages.yaml](config/feedback_messages.yaml).
An allowed short post costs one inference (about 11 ms). Multi-sentence posts
also score every sentence in one batched call.

The interfaces with the other two tracks are documented in [../contracts/](../contracts/).

## Quick start

```bash
cd flask_api
python -m venv .venv && .venv/Scripts/activate      # Windows; on Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q

# The live model is committed under models/v4 via Git LFS (install from https://git-lfs.com,
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

- **Any sentence over the reject boundary rejects the post** (`decided_by: "sentence"`).
- With a middle band configured (`allow_max < reject_min`), a middle-band sentence in
  an otherwise allowed post asks for a revision (`revise_on_middle`).
- Sentences are split only at `. ! ?` followed by a space, so URLs and file names
  ("example.com", "catalogue.pdf") stay whole. Fragments under 3 words ("Thanks.",
  a one-word heading) are merged into the neighbouring sentence, because scored alone
  a lone word has no context and its score is noise.

v4 is **batch-invariant** (`batch_invariant: true` in its meta), so all sentences are
scored in batched calls. v3's dynamic INT8 changed scores by up to 0.63 when batched,
which forced one call per sentence. Turn the scan off with `SENTENCE_SCAN=0`.

## PDF uploads

`POST /v1/moderate/pdf` takes a PDF as multipart `file`, extracts its text with `pypdf`, and
runs the ordinary pipeline over it. The response adds a `pdf` block mapping character offsets
to page numbers, so each issue can be shown on the page it came from.

Two decisions in [app/pdf.py](app/pdf.py) are worth knowing:

- **A page we can't read is refused, not allowed.** Scanned pages yield no text, and a
  moderator that "checks" empty text approves anything — uploading a screenshot of a scam would
  defeat the whole system. A page carrying images but almost no extractable text counts as
  unreadable and the upload is rejected (`422 pdf_text_not_extractable`) naming those pages.
  `pdf.reject_unreadable_pages: false` accepts them, and then you are publishing pages nothing
  checked.
- **OCR is tried on those pages first** (see below), so a legitimate scanned brochure is read
  and judged rather than turned away.
- **Wrapped lines are rejoined.** PDFs break paragraphs at every visual line and the sentence
  scan splits on newlines, so raw extracted text would arrive as dozens of fragments, each
  judged without its context. Hyphenated splits ("manu-\nfacturer") are joined too, while real
  paragraph breaks, bullets and headings are kept.

Long documents are rejected rather than truncated, because a truncated check that answers
"allowed" is worse than no check. Limits (size, pages, characters) are under `pdf` in
`settings.yaml`; `PDF_UPLOAD=0` disables the endpoint.

### OCR for scanned pages

Pages with no extractable text are rasterised and read with **RapidOCR** ([app/ocr.py](app/ocr.py)):
PaddleOCR's models exported to ONNX and run through the onnxruntime this service already ships —
no PyTorch, no Paddle, no system binary, and the weights live inside the package so nothing is
downloaded at run time (Apache 2.0). Pages are rendered with `pypdfium2` rather than pulled out
as embedded images, because scans arrive in encodings (CCITT G4, JBIG2) that image extractors
often refuse, and a decode failure would turn away a legitimate document.

Measured end to end on this service:

| Upload | Latency | Decision |
|---|---|---|
| Text PDF (OCR not needed) | 106 ms | allow |
| Scanned scam flyer | 1.4 s | **reject** 0.999 (OCR confidence 0.99) |
| Same, at 45% resolution | 0.7 s | **reject** 0.999 |
| Same, skewed 7° | 0.9 s | **reject** 0.999 |
| Scanned legitimate catalogue | 0.6 s | **allow** — previously refused |
| Blank/illegible scan | 0.5 s | still refused |

**It is not a security control.** OCR converts refusals into decisions; it never converts a
refusal into an approval. A page it reads with too few characters (`min_chars`) or too little
confidence (`min_confidence`) stays unreadable, so degrading an image until OCR fails gains
nothing. Everything is bounded — pages per upload, render DPI, pixels per page — because
decoding untrusted images is real attack surface. `PDF_OCR=0` turns it off, and the optional
packages being absent disables it gracefully rather than failing uploads.

Because OCR costs ~0.5–1.5 s per page against an 11 ms model call, a deployment expecting many
scans should run the PDF endpoint in async mode.

## Triage pre-filter (`triage`)

The scan above is the expensive part of an ordinary, allowed post: every sentence is a model
call, and nearly all come back safe. Layer 1.5 is a logistic regression over hashed word and
character n-grams ([app/triage.py](app/triage.py)) that clears the obviously-safe spans in
about 0.1 ms each, so only the rest reach DeBERTa.

- **It is distilled from the model itself**, not trained on the dataset labels: the target is
  "would DeBERTa have flagged this span?", because a span the model would allow is free to skip.
- **The threshold is chosen by decision impact, not by span accuracy.** `training/tune_triage.py`
  runs every held-out text through the real pipeline at each candidate threshold and takes the
  highest one that changes no decision, halved for margin. At 0.4 two harmful posts started
  slipping through; nothing changes at 0.2; the shipped value is **0.1**.
- **Leetspeak can't slip past it**: each span is also scored as its de-obfuscated reading, and
  it is only cleared when every reading is below the threshold.
- Cleared spans still appear in `sentence_scores`, marked `"by": "prefilter"`, and can never
  escalate a decision.

Measured on 619 held-out texts (`models/triage-v1/triage_meta.json`):

| | model calls | ms per text |
|---|---|---|
| Allowed posts (most traffic) | −73% | 50 → 25 |
| Allowed posts over 600 characters | −84% | 267 → 101 |
| Rejected posts | −6% | unchanged (the word scan dominates, and the filter doesn't touch it) |

Retrain it whenever the model changes — the bundle records the threshold it was measured at,
and the loader refuses a bundle whose feature version doesn't match the code. `TRIAGE=0`
turns it off; decisions must not change, only latency.

## Word-by-word scan (`word_scan`)

- **Word windows:** a sentence longer than 40 words (a pasted run-on paragraph) is also
  scored in overlapping 40-word windows, so a harmful phrase in the middle isn't diluted.
- **Trigger words:** for every span the model flags, each word is removed in turn and
  the span re-scored (`word_scores`). Words whose removal lowers the risk by ≥ 0.10 are
  the trigger words. If none does (harm spread over several words, as in "join by
  paying a small registration fee and earn 50000"), a greedy search finds the smallest
  set of words whose removal brings the span back to ≤ 0.5. Trigger words are attached
  to the issue as `words` and shown in bold to the author. Allowed posts pay nothing for this.
- **Leetspeak:** words with a digit or symbol between letters ("F1rst c0py R0lex") are
  also read as plain letters, character for character, and the riskier reading counts.
  Product codes and quantities ("330W", "A16", "5kg", "3pm") are left alone.

Turn it all off with `WORD_SCAN=0`.

## Score calibration (`label_weights`)

`risk = Σ weight(label) × P(label)`, with the weights taken from the bundle's
`model_meta.json` (an operator override in `settings.yaml` still wins).

- **v4:** labels `safe`/`unsafe`, weights 0/1, so risk = P(unsafe). It is temperature-calibrated in training (test ECE 0.006), which makes 0.5 a real boundary. See [../training/README.md](../training/README.md).
- **v3 (rollback):** labels `safe`/`review`/`reject`, with review weighted **0.25** in its own `model_meta.json` because that class fires on almost any prose that isn't a product listing. **Only `models/v4` and `models/triage-v1` are tracked in git** — v1 and v3 are kept on the machines that need them (and remain in this repo's LFS history from earlier commits). To roll back, restore a v3 bundle into `flask_api/models/v3/` and set `model.dir: models/v3` **and** thresholds 0.30/0.70. Measured with `scripts/compare_models.py models/v3 models/v3 --old-review-weight 0.5 --new-review-weight 0.25`:

| review weight | legit allowed | harmful caught | defects | sigma level | McNemar p |
|---|---|---|---|---|---|
| 0.50 | 4/36 | 17/18 | 33/54 | 1.22 | |
| **0.25** | **36/36** | 12/18 | **6/54** | **2.72** | < 0.0001 |

Re-measure for every new model. See `docs/MODEL_IMPROVEMENT_GUIDE.md`.

## Abuse aimed at someone

Two checks stop comments that attack another person or business:

- **Gate rule `abuse.targeted`** (instant, no model): a word that points at someone
  (he, she, his, her, him, they, them, their…) and an insult or profanity **in the
  same sentence, in either order** ("he is a liar", "what a liar he is") is rejected.
  This is the senior-review policy: nobody may point abuse at an individual.
- **The model covers contempt the wordlist can't.** Words like *absurd*, *ridiculous* or
  *nonsense* are deliberately **not** on the blocklist, because they are ordinary when they
  describe a price or a delay, and the gate can only see that a pronoun and a term share a
  sentence — not which one the term is about. Adding them would reject "the price he quoted
  was absurd". The model is trained on both sides instead, so "his way of thinking is absurd"
  scores 0.999 while "the price they quoted was absurd" scores 0.002.
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
`python scripts/compare_models.py models/v3 models/v4 --no-builtin --testset ../training/eval_handwritten.csv`
runs labelled texts through the full pipeline with each model (each with its own
recommended thresholds), lists every decision that changes, and reports McNemar's p-value.
`python ../training/e2e_eval.py` gives allow and catch rates per held-out set.

After changing the model, thresholds or rules, run
`python scripts/verify_demo_sentences.py` against a running server. It checks
every demo sentence against the decision the demo guide promises.

## Endpoints

| Method | Path | |
|---|---|---|
| POST | `/v1/moderate` | Moderates one piece of content. Returns `200` with the result, or `202` pending in async mode. |
| POST | `/v1/moderate/pdf` | Moderates an uploaded PDF (multipart `file`). Extracts the text, runs the same pipeline, and adds a `pdf` page map. Refuses pages it can't read. |
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
