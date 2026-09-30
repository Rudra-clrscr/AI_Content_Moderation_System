# Contract: Moderation API and result payload (API team → Data team)

**Status:** DRAFT. Proposed by the API team and needs the Data team's sign-off.
**Producer:** `flask_api/app/pipeline.py` (`Pipeline._build`)
**Hand-off point:** `flask_api/app/sinks.py` (`ResultSink.emit(result)`)

## Request: `POST /v1/moderate`

```json
{
  "content": "Premium basmati rice exporter, FSSAI certified.",
  "content_type": "product_listing",
  "content_id": "LST-10293"
}
```

| Field | Required | Notes |
|---|---|---|
| `content` | yes | A non-empty string of at most `limits.max_chars` characters (default 25,000, enough for a 20,000-character article body plus title and links). |
| `content_type` | yes | The dashboard surface the member is publishing from — one per tab. It does **not** change how the text is scored; it selects the wording of `feedback` ("Your proposal needs a few changes") and is stored with the decision so a record can be traced back to where it was made. |
| `content_id` | no | The platform's own ID, as a string or an integer. It's always returned as a string. |

### Surfaces and what to send

Every surface that publishes member-written text goes through the same endpoint. Join the
form's inputs into one `content` string so harm split across fields is still caught — a clean
title with the scam in the description is still a scam. `moderation-client.js`'s
`buildContent` / `moderateFields` does this and keeps a map back to each input for highlighting.

| Dashboard tab | `content_type` | Send |
|---|---|---|
| Articles | `article` | title + body + link URLs |
| Add Business | `business_profile` | name, about, services, address |
| Videos | `video` | **title + description + tags.** The footage itself is *not* checked — see below |
| Requests | `request` | title + requirement + category |
| Proposals | `proposal` | title + proposal text + terms |
| Business Proposals | `business_proposal` | as above |
| — | `product_listing`, `advertisement`, `post` | existing surfaces |

**Pictures and footage are not inspected.** This service reads text. There is no visual model
here — no nudity, violence or counterfeit detector, and no frame or audio analysis — so a
video's title and description are moderated while the footage is not, and an image is OCR'd
for words while the picture is not classified.

The platform's current policy is to publish anyway rather than close those tabs:
`media.allow_unchecked_video` is **on**, and such a result says so in the record
(`checked: false`, `visual_content_checked: false`) instead of passing the file off as clean.
Turn the flag off the day footage must not go out unwatched. Until a visual model exists,
uploaded pictures and video are covered by human reporting, not by this service.

## Request: `POST /v1/moderate/media` (and `/v1/moderate/pdf`)

`multipart/form-data` upload of **one** attachment — post one file per request, so each gets
its own decision and its own audit row. Its text is extracted and then moderated by exactly
the same pipeline, so a brochure is judged by the same rules as a pasted post.
`/v1/moderate/pdf` is the original path and still works; both accept every kind below.

Anything published with a post has to be checked, or the scam simply moves into the picture.
**What can be read differs by kind, so the rule differs too:**

| Kind | What is read | Rule |
|---|---|---|
| `pdf` | Text, with OCR for scanned pages | A page that can't be read is **refused** |
| `image` | OCR only | Text is moderated. **The picture itself is never classified** — there is no nudity/violence/counterfeit detector here — so an image with no text is **allowed**, and the result says `visual_content_checked: false` |
| `video` | Nothing | **Allowed unchecked** (`checked: false`) while `media.allow_unchecked_video` is on, as it is in the shipped config; **refused** when it is off |
| anything else | — | `422 media_unsupported` |

The kind comes from the file's leading bytes, never its name: a `.png` that is really a PDF is
treated as a PDF, and an executable renamed `.jpg` is refused.

Every response carries a `media` block:

```json
"media": {"filename": "flyer.png", "kind": "image", "text_found": true,
          "ocr_confidence": 0.9937, "visual_content_checked": false}
```

A file with nothing to read (a photo with no text, or an unchecked video) still returns a
normal `allow` result and is still written to the audit log, so the record shows both what was
published and that nothing in it could be read.

**Why an image with no text is allowed but a blank PDF page is not:** a product photo with no
writing on it is completely ordinary, and refusing those would break the feature for every
honest seller. A PDF page with no text is not ordinary — it is a scan, and scans are how a
screenshot of a scam arrives. The asymmetry is deliberate.

Media errors: `413 media_too_large`, `422 media_unsupported` / `media_not_checkable` /
`media_unreadable`, plus all the PDF codes below.

### The PDF case in detail

| Field | Required | Notes |
|---|---|---|
| `file` | yes | The PDF. Limits are in `settings.yaml` under `pdf` (default 10 MB, 100 pages, 100,000 extracted characters). |
| `content_type` | no (`article`) | As above. |
| `content_id` | no | As above. |

The response is the payload below plus a `pdf` block:

```json
"pdf": {
  "filename": "catalogue.pdf",
  "page_count": 4,
  "extracted_chars": 5120,
  "pages": [{"page": 1, "start": 0, "end": 1180, "chars": 1180},
            {"page": 2, "start": 1182, "end": 2300, "chars": 1118,
             "source": "ocr", "confidence": 0.9937}, …],
  "unreadable_pages": [],
  "ocr_pages": [2]
}
```

A page carries `"source": "ocr"` and a `confidence` when its text was read off the pixels
rather than taken from the PDF; `ocr_pages` lists them, and the key is absent when none were.

`pages` maps character offsets in `content` to page numbers, so an issue in `feedback.issues`
can be shown on the page it came from: find the page whose `start <= issue.start < end`.

**Pages that can't be read are refused, not allowed.** A scanned page holds no extractable
text, and content that can't be read can't be checked — allowing it would let a screenshot of a
scam through untouched. Such uploads return `422 pdf_text_not_extractable` with
`unreadable_pages`. Set `pdf.reject_unreadable_pages: false` to accept them anyway, in which
case the pages are listed in `pdf.unreadable_pages` and the entry's `"readable": false`, and
you are publishing pages the moderator never read.

**OCR is attempted on those pages first** (`pdf.ocr` in `settings.yaml`, RapidOCR on
onnxruntime), so a scanned brochure is read and judged rather than turned away. It only ever
widens what can be accepted: a page OCR reads with too little text or too low confidence stays
unreadable and is still refused, so degrading an image to defeat OCR gains nothing. Expect
~0.5–1.5 s per OCR'd page against ~100 ms for a text PDF; `PDF_OCR=0` turns it off.

Long documents are **rejected, never truncated** (`413 pdf_text_too_long`): truncating would
leave the tail unchecked while still answering "allowed".

PDF errors: `400 invalid_file`, `413 pdf_too_large` / `pdf_too_many_pages` / `pdf_text_too_long`
/ `content_too_long`, `422 pdf_invalid` / `pdf_encrypted` / `pdf_unreadable` / `pdf_empty` /
`pdf_text_not_extractable`, `404 not_found` when `pdf.enabled` is false.

In async mode the `202` response carries the `pdf` block, but the stored result fetched from
`GET /v1/moderate/{request_id}` does not — the queue carries the extracted text, not the file.

## Responses

**`200` final result.** This comes back in sync mode, or in either mode when the
Layer 1 gate blocks the content:

```json
{
  "schema_version": "1.5",
  "request_id": "5b0c7e0e-…",
  "status": "completed",
  "content_id": "LST-10293",
  "content_type": "product_listing",
  "content": "Premium basmati rice exporter, FSSAI certified.",
  "content_sha256": "9f2c…",
  "decision": "allow",
  "decided_by": "model",
  "risk_score": 0.041,
  "predicted_label": "safe",
  "label_scores": {"safe": 0.959, "spam": 0.03, "fraud": 0.011},
  "gate_matches": [],
  "targeted_segments": [],
  "sentence_scores": [],
  "word_scores": [],
  "feedback": null,
  "gate_version": 2,
  "model_version": "deberta-v3-small-bonc-v4",
  "thresholds": {"allow_max": 0.5, "reject_min": 0.5},
  "latency_ms": {"gate": 0.02, "inference": 11.4, "total": 11.6},
  "decided_at": "2026-09-25T14:03:11.204+00:00"
}
```

| Field | Notes |
|---|---|
| `decision` | `allow` / `revise` / `reject`. *Changed in 1.2:* `review` was replaced by `revise`. There's no human-review queue; `revise` content isn't published, and its author is shown what to fix (see `feedback`) and can resubmit. *With v4* the thresholds are one boundary (`allow_max = reject_min = 0.5`): risk ≤ 0.5 is `allow`, risk > 0.5 is `reject`, and the author rewrites the highlighted parts and resubmits. `revise` then only comes from a gate `revise` rule. |
| `decided_by` | `gate` means a Layer 1 rule decided: a `block` rule (model skipped), or a `revise` rule on content the model would have allowed. After a block, `risk_score`, `predicted_label`, `label_scores`, `model_version` and `latency_ms.inference` are then `null`. `model` means the model's score decided it. `targeted` means the whole text passed, but a sentence aimed at someone scored in the revise or reject band (see `targeted_segments`). `sentence` (added in 1.3) means one sentence, scored on its own, reached reject level (see `sentence_scores`). |
| `gate_matches` | A list of `{rule_id, category, action, spans}`. `spans` are `[start, end]` character offsets into `content` (added in 1.2). `block` rejects, `revise` sends the content back to its author, and `flag` is recorded only. |
| `sentence_scores` | *Added in 1.3.* For posts with 2 or more sentences: every sentence scored on its own, up to `sentence_scan.max_sentences`, as `{start, end, risk_score}` (offsets into `content`). *1.5:* an entry with `"by": "prefilter"` was scored by the linear triage filter instead of the model (`app/triage.py`) — its `risk_score` comes from that filter, is far below `allow_max`, and never contributes to the decision. Entries without `by` are model scores, as before. Any sentence in the reject band (v4: risk > 0.5) rejects the post (`decided_by: "sentence"`). *1.4:* sentences longer than `word_scan.window_words` words are also scored in overlapping word windows, listed here with `"kind": "window"` (so a long run-on sentence can't dilute a harmful phrase); these can appear even for a single-sentence post. Empty for short single-sentence posts, gate blocks, posts already rejected, or when the scan is off. |
| `word_scores` | *Added in 1.4.* The word-by-word scan, for each span the model flagged (risk above `allow_max`): every word as `{start, end, contribution}`, where `contribution` = risk of the span minus risk of the span with that word removed. The *trigger words* (in `feedback.issues[].words`) are the words with `contribution >= word_scan.min_contribution` (default 0.10). When no single word reaches that (the harm is spread over several words, or the score is saturated at 0.999), they are the smallest set, found greedily and at most `max_triggers`, whose removal brings the span back to ≤ `allow_max`. Long spans are analysed in their riskiest word window. Empty when nothing was flagged. |
| `feedback` | *Added in 1.2.* Author-facing guidance, and `null` when allowed. `{title, message, issues, categories?}`. Each issue is `{start, end, source, message, rule_id?, category?, risk_score?, words?}`, where `start`/`end` are offsets into `content` for the client to highlight, `source` is `rule` or `model`, and `message` is the instruction to show. *1.4:* `words` lists the trigger words inside a model issue as `[start, end]` offsets; show them emphasised inside the highlighted sentence. For `reject`, `categories` names the policy areas. Since 1.4 the shipped config sets `highlight_on_reject: true`: with v4 there is no revise band, so a rejected author rewrites and needs to see what to change. Set it to `false` to show only the policy area. Wording lives in `flask_api/config/feedback_messages.yaml`. |
| `targeted_segments` | *Added in 1.1.* Sentences that mention someone (he, she, they, their…), scored separately: a list of `{start, end, risk_score, predicted_label}`, where `start`/`end` are character offsets into `content`. The text itself isn't repeated, so logs stay free of content. Empty when the check didn't run: gate block, whole text already rejected, no such sentence, or the check disabled. Not stored by the current SQL table; the Data team can add an `NVARCHAR(MAX)` JSON column if they want it. |
| `thresholds`, `model_version`, `gate_version` | These are included so every logged decision can be reproduced and audited after thresholds or models change. |

**`202` pending.** This comes back in async mode when the gate didn't block:

```json
{"request_id": "5b0c7e0e-…", "status": "pending", "content_id": "LST-10293",
 "content_type": "product_listing", "status_url": "/v1/moderate/5b0c7e0e-…"}
```

`GET /v1/moderate/{request_id}` returns the full result above once it's done. Until
then it returns `{"status": "pending"}`, or `{"status": "failed", "error": …}` if the
job failed.

**Errors** always look like `{"error": {"code": "...", "message": "..."}}`:

| Status | Codes |
|---|---|
| 400 | `invalid_json`, `invalid_content`, `invalid_content_type`, `invalid_content_id` |
| 413 | `content_too_long` |
| 503 | `model_not_ready` (sync), `queue_unavailable` (async) |
| 500 | `inference_failed` |

## Who writes to SQL Server

Whichever process produces the **final** result calls `sink.emit(result)` exactly once:

- In sync mode, and for gate blocks, that's the Flask process.
- In async mode, when the model runs, that's the Celery worker (`app/tasks.py::run_moderation`).

`SqlServerSink` (`app/sinks.py`) inserts one row into `dbo.moderation_events`
(`sql/create_table.sql`). `content` itself is not stored, only `content_sha256`.
Set `result_sink: log` to write JSON log lines instead.

A storage failure never costs the caller its decision:

- **Flask:** the sink is wrapped in `FailSafeSink`. A failed write is logged as
  `db_write_failed`, with the full result minus content so it can be replayed,
  and the API still returns `200` with the decision.
- **Celery worker:** a failed write is handed to the `moderation.persist_result`
  task, which retries only the write, with backoff, up to 8 times. Inference
  doesn't run again.
- **Duplicates:** inserts are idempotent on `request_id`. A retried or
  redelivered job that hits the primary key is treated as already stored.

Connection settings come from the environment or `flask_api/.env`: `DB_SERVER`,
`DB_NAME`, `DB_USER`/`DB_PASSWORD` (if these are unset, Windows authentication is
used), `DB_DRIVER`, `DB_TRUST_SERVER_CERTIFICATE` and `DB_TIMEOUT_SECONDS`.

## Articles: what to send

Send the title, body and link URLs as one text, and keep the offsets of each
part so feedback can be mapped back to the fields:

```
<title>

<body as plain text>[

<link URL 1>
<link URL 2>...]
```

Link URLs must be included, because a scam domain hidden behind "click here"
isn't part of the visible text. `flask_api/app/static/articles/moderation-client.js`
does all of this (`buildArticleContent`, `mapIssues`) and can be copied as-is.

## Showing feedback to the author (platform front end)

When `decision` is `revise`, show `feedback.title` and `feedback.message`, then
render `content` with each `feedback.issues[i]` range (`start` to `end`)
highlighted, and list each issue's `message`. Keep the author's text editable,
and resubmit it to `/v1/moderate` as a new request. The demo page
(`/demo`, `flask_api/app/static/demo.html`, function `nudge`) is a working
reference.

## Open questions for the Data team

1. In sync mode the insert still happens inside the request. Should it move to a background writer or queue so API latency doesn't include the DB round-trip?
2. ~~Should raw `content` be stored?~~ Settled: hash only.
3. ~~Should `request_id` be the primary key?~~ Settled: yes.
4. Queue and broker names: I've assumed queue `moderation` and Redis DBs 0 (broker) and 1 (results). Are those right?
5. Do the four `content_type` values match your schema's enum?
