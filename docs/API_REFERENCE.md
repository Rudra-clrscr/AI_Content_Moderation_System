# BONC Moderation API — Reference

Every HTTP endpoint this service exposes, what it does, and how it behaves.

**Style:** REST over JSON, built on Flask 3.1 (`flask_api/app/`), served through WSGI
(`gunicorn -w 4 wsgi:app` on Linux, `waitress-serve` on Windows). There is no SOAP, no
GraphQL and no gRPC anywhere in this project.

**Base URL:** `http://<host>:8000` by default (`flask_api/wsgi.py`).

**Versioning:** everything functional sits under `/v1/`. The unversioned paths are
`/health`, `/ready`, `/docs`, `/openapi.yaml`, `/demo` and `/articles`.

---

## Table of contents

1. [Conventions](#1-conventions)
2. [**The moderation API — text**](#2-the-moderation-api--text) ← the main endpoint
3. [**The moderation API — images, PDFs and video**](#3-the-moderation-api--images-pdfs-and-video)
4. [The result payload, field by field](#4-the-result-payload-field-by-field)
5. [Operational endpoints](#5-operational-endpoints)
6. [Admin — authentication](#6-admin--authentication)
7. [Admin — moderation levels and settings](#7-admin--moderation-levels-and-settings)
8. [Admin — audit log](#8-admin--audit-log)
9. [Admin — blocked clients](#9-admin--blocked-clients)
10. [Admin — image dataset and training](#10-admin--image-dataset-and-training)
11. [Admin — visual policy](#11-admin--visual-policy)
12. [Appendix: enums, limits, error codes](#12-appendix)

---

## 1. Conventions

### The company envelope

Every endpoint answers the **BONC Network `ApiResponse<T>`** envelope from the official
Swagger (`https://api.boncnetwork.com/swagger/index.html`) — three keys, and no others:

```json
{
  "Success": true,
  "Message": "Content approved for publication.",
  "Data": { "...": "the payload" }
}
```

**The payload is in `Data`, and only in `Data`.** All 23 `*ApiResponse` schemas in the
company specification declare `additionalProperties: false`, so a fourth top-level key makes
the response invalid against the contract this service exists to satisfy. An earlier revision
of this service also sent a `Type` field and repeated the whole payload at the top level;
`Type` appears nowhere in the company specification, and the repeated copy broke the
`additionalProperties` rule, so both are gone.

`Message` is prose and may be reworded — branch on `Success` and on `Data`, never on it. It is
deliberately honest about what happened: an approval that nothing actually checked (moderation
switched off for the surface, or a file nothing could read) says so instead of claiming the
content was approved on inspection.

#### The verdict: "approved" and "reject"

The .NET backend's publishing DTOs — `AdminArticleActionDto` and `ArticleAdminActionDto` —
take an `Action` and a `Note`, so `Data` carries both under those names:

* **`"approved"`** — the content passed. The .NET server publishes the entity.
* **`"reject"`** — refused. The .NET server keeps the draft and stores `Note`.

> ⚠️ **The two spellings are this service's choice, not the specification's.** The company
> spec declares `Action` as an unconstrained `string` — no enum, no pattern — and the words
> `approved` and `reject` appear in it nowhere. The one place the company API *does* constrain
> a comparable field, it uses `^(Accept|Reject|Message)$`, in **PascalCase**. Confirm the
> casing the .NET side expects before relying on it: a mismatch does not raise an error, it
> simply never matches, and content stays in draft.

Inside `Data`:

| Key | Value |
|---|---|
| `Action` | `"approved"` \| `"reject"` — and `"pending"` on the async `202`. **Branch on this one.** |
| `Decision`, `action`, `verdict`, `approval_status` | the same verdict under the names different callers look for. All four always agree with `Action`. |
| `decision` | `"approved"` \| `"revise"` \| `"reject"`. Not a second name for `Action`: an allow becomes `"approved"`, but a **`"revise"` stays `"revise"`**, because the author-facing surfaces must tell "fix the highlighted part and resubmit" apart from a flat refusal. A caller reading `decision` must treat `"revise"` as a refusal. |
| `legacy_decision` | the pipeline's own word — `"allow"` \| `"revise"` \| `"reject"` — always. This is what the audit log stores, so a log row and the response that produced it say the verdict differently by design. |
| `status` | the request's lifecycle, `"completed"` or `"pending"` — *not* the verdict. |
| `Note` | the author-facing reason, for a remark column. **Policy wording** from `config/feedback_messages.yaml`, never the submitted text — that is only ever in `Data.content`. `null` when approved. |

There is no `Status` key. `Action` is the verdict; `status` (lower case) is the lifecycle.
(`Status` *is* a real company field, but on `AdminJobActionDTO`, where the Job surface uses
`Status` + `Note` where the article surfaces use `Action` + `Note`.)

#### Errors — one shape, at last

A refusal is the same envelope with `Success: false`, and the machine-readable detail in
`Data`:

```json
{
  "Success": false,
  "Message": "unknown 'content_type'",
  "Data": {
    "code": "invalid_content_type",
    "message": "unknown 'content_type'",
    "allowed": ["business_profile", "product_listing", "..."]
  }
}
```

**`Data.code` is the stable name of the refusal** — branch on it, not on `Message`. Extra keys
(`allowed`, `limit`, `request_id`, `kind`, `text_readable`, `conflicts`) appear inside `Data`
per error, where they help the caller recover.

This replaces the two incompatible error shapes this API used to have, where `error` was a
bare string from `_err()` on the admin routes and an object from `_error()` on the moderation
routes — so that `GET /v1/admin/moderation-levels` answered `401` in one shape and its own
`404 no_such_surface` in the other. **There is no top-level `error` key any more.** A client
still reading `body.error` or `body.error.code` needs updating to `body.Data.code`.

A refusal of *content* is not an error: `Data.Action: "reject"` comes back `200` with
`Success: true`. The request succeeded; the content was refused.

Unhandled Werkzeug `HTTPException`s are funnelled into the same envelope by `_http_error`, so
a 404 or a 405 never returns Flask's HTML error page.

#### What is *not* enveloped

The **admin endpoints' success responses** are unchanged and return their payload bare. They
are the dashboard's own API, called by `admin.js` from the same origin and not by the .NET
server, so wrapping them would be churn with no consumer. Their *errors* do use the envelope
above, like everything else.

### Authentication — four tiers

| Tier | Endpoints | How |
|---|---|---|
| **Service** | `/v1/moderate`, `/v1/moderate/media`, `/v1/moderate/{request_id}` | `Authorization: Bearer <SERVICE_TOKEN>` — **or open, if `SERVICE_TOKEN` is unset** |
| **Open** | `/health`, `/ready`, `/demo`, `/articles` | none |
| **Admin** | most of `/v1/admin/*` | session cookie **or** `X-Admin-Token` |
| **Token only** | `POST /v1/admin/model/reload` | `X-Admin-Token` only |

#### The service tier

This service runs on its own AI/ML host, beside the models rather than beside the caller, and
the main .NET server reaches it over the network. So the submission endpoints take a bearer
token in `Authorization` — declared exactly as the company API declares its own `Bearer`
scheme (an `apiKey` in the `Authorization` header, not HTTP `bearer`), so the calling side
reuses the handler it already has.

The status lookup is in this tier too: it hands back a whole moderation result to anyone who
can guess a request id. The two probes are deliberately left open so a load balancer can reach
them without a secret.

> ⚠️ **With `SERVICE_TOKEN` unset, these endpoints are open.** Local checkouts and the test
> suite depend on that, so it stays — but it is a hole to close on deployment, not a default
> to lean on. From outside, a deployment that forgot the token looks exactly like one that did
> not, so `GET /ready` reports `service_auth` as `enforced` or `open`. **Assert `enforced` in
> your deploy check.**

Enforced in `_require_service_token()` (`app/api.py`), from the `before_request` hook that
already resolves the caller's address — so a new endpoint added to `SERVICE_ENDPOINTS` is
covered without remembering a decorator. The comparison is `hmac.compare_digest`, and a
refusal says only `unauthorized`: which half of the header was wrong is the one thing worth
knowing to someone guessing.

`check_admin()` in `app/admin_auth.py` implements the admin tier:

- A logged-in session (Flask cookie, set by `POST /v1/admin/auth/login`) is accepted.
  On any non-GET method the request must also carry **`X-Requested-With: bonc-admin`**
  or it is refused `403 forbidden`. This is the CSRF defence — a custom header cannot be
  set cross-origin without a preflight the server never answers.
- Failing that, `X-Admin-Token: <ADMIN_TOKEN>` is compared with `hmac.compare_digest`.
- If no admin account exists *and* `ADMIN_TOKEN` is unset, admin endpoints return
  `404 not_found` rather than `401` — they are considered disabled, not protected.

`POST /v1/admin/model/reload` deliberately does **not** use `check_admin()`. It accepts
the token only, so a logged-in dashboard session cannot hot-swap the model.

### Client identification and blocking

A `before_request` hook on the moderation blueprint resolves the caller's address once
per request via `app/client_address.py`, honouring `X-Forwarded-For` to the depth of
`trust_proxy_hops` (default `0`). The resolved address is attached to every audit row.

For both **submission** endpoints only (`/v1/moderate` and `/v1/moderate/media`) the
address is checked against the blocklist; a hit returns
`403 client_blocked` with a deliberately vague message that names neither the address
nor the scope. Admin endpoints are exempt, so an operator who blocks their own office
network does not lock themselves out of the screen that would undo it.

If the blocklist cannot be read, submissions **carry on** and the failure is logged —
it is a moderation convenience, not an authentication boundary.

> **Operational note.** The service warns once per process if requests arrive with
> `X-Forwarded-For` while `trust_proxy_hops` is `0`, because every audit row would
> otherwise record the proxy's address instead of the author's.

### Request size

`MAX_CONTENT_LENGTH` is computed at startup as the larger of the JSON limit
(`max_chars × 4 + 4096`) and `media.max_bytes + 8192`.
Exceeding it yields a Werkzeug `413`.

---

# 2. The moderation API — text

This is the endpoint the platform calls on every piece of member-written text.

## `POST /v1/moderate`

**Auth:** none. **Content-Type:** `application/json`.

### Request

```json
{
  "content": "Premium basmati rice exporter, FSSAI certified.",
  "content_type": "product_listing",
  "content_id": "LST-10293"
}
```

| Field | Required | Type | Notes |
|---|---|---|---|
| `content` | yes | string | Non-empty after stripping. Max `limits.max_chars` (**25,000**). |
| `content_type` | yes | enum | One of the eleven surfaces below. Unknown values are refused. |
| `content_id` | no | string \| int | Your platform's own ID. Always returned as a string. |

**`content_type` does not change how the text is scored.** It selects three things: the
moderation level the admin set for that surface, the author-facing wording in
`config/feedback_messages.yaml` ("Your *proposal* needs a few changes"), and whether
surface-scoped gate rules and the grievance check apply.

#### Joining fields into one `content`

Send the whole form as one string. Harm split across fields is still harm — a clean
title with the scam in the description is still a scam, and moderating fields separately
misses it. The reference client (`app/static/articles/moderation-client.js`,
`buildContent` / `moderateFields`) joins the inputs and keeps a map back to each one so
the returned offsets can be highlighted in the right box.

| Surface | `content_type` | Send |
|---|---|---|
| Articles | `article` | title + body + link URLs |
| Network articles | `network_article` | as above, published to the network rather than a company page |
| Add Business | `business_profile` | name, about, services, address |
| Videos | `video` | title + description + tags (**not** the footage) |
| Requests | `request` | title + requirement + category |
| Proposals | `proposal` | title + proposal text + terms |
| Business Proposals | `business_proposal` | as above |
| Group posts | `group_post` | the written fields |
| Feed / listings / ads | `post`, `product_listing`, `advertisement` | the written fields |

### Responses

| Status | When |
|---|---|
| `200` | A decision was reached (allow, revise **or** reject — a refusal is still a 200). |
| `202` | Async mode only: queued. Body carries `status: "pending"` and `status_url`. |
| `400` | `invalid_json`, `invalid_content`, `invalid_content_type`, `invalid_content_id` |
| `403` | `client_blocked` |
| `413` | `content_too_long` |
| `503` | `model_not_ready` (model never loaded) or `queue_unavailable` (async) |
| `500` | `inference_failed` — carries `request_id` so the log can be found |

> **A refusal is not an error.** `decision: "reject"` comes back `200`. Reserve your
> error handling for 4xx/5xx; read `decision` to decide whether to publish.

### How a decision is reached

`app/pipeline.py::Pipeline.moderate` runs these in order and stops at the first that
decides. `decided_by` in the response names the one that did.

**0. Is this surface moderated at all?**
`levels.for_type(content_type)`. If the admin disabled the surface, **nothing runs** —
not the model, not even the Layer 1 gate. The result is `allow` with
`decided_by: "disabled"` and a null `risk_score`, so an audit reader can tell
"published unchecked" from "checked and clean". Returns `200` immediately.

**1. Layer 1 gate** (`app/gate.py`) — always runs, always inline. Regex and term-set
rules from `config/gate_patterns.yaml`, each with an action of `block`, `revise` or
`flag`, optionally scoped to particular surfaces. A `block` match short-circuits:
`decided_by: "gate"`, no model call, no queue. Cheap and deterministic.

**2. Grievance check** (`app/grievance.py`) — the public-surface complaint model, asked a
*different question* from the main one: "is this a customer-support complaint or a public
outcry?" It runs only when **both** hold:

- the effective `reject_min` is **≤ 0.50** (standard or stricter — on lenient levels
  public commercial friction is permitted by policy, so the check is bypassed), and
- `content_type` is one of its configured surfaces, default `article`, `post`,
  `advertisement`.

A surface it does not list — including a brand-new one nobody added — is **not checked**,
which is the opposite of how the gate treats unknown surfaces. **`group_post` and
`network_article` are in exactly that position today**: both are public surfaces, neither
is on the list, so a product complaint posted to a group is scored by the general model
with no complaint cover. See 12.5. A refusal here is
`decided_by: "grievance"`, never `"model"`, so an audit reader can see which model
objected; the main model would publish the same words in a proposal.

**3. Whole-text model** (`app/model.py`, ONNX). Produces a calibrated `risk_score` in
[0,1] routed against the active thresholds into allow / revise / reject. If word-scan
de-obfuscation is on, the text is also scored with leetspeak restored (`F1rst c0py` →
`First copy`) and **the riskier of the two readings counts**.

**4. Targeted check** (`app/targeted.py`) — sentences aimed at a person or company are
scored on their own and can escalate the verdict. `decided_by: "targeted"`.

**5. Sentence scan** — every sentence, plus sliding word windows over long ones, scored
individually. **Any single sentence at reject level rejects the whole post**, whatever
the whole-text score says. This is deliberate product policy, not a defect: a clean
2,000-word guide scores 0.0004 and is allowed; the same guide with one unacceptable
sentence appended still scores 0.0004 as a whole and is refused with
`decided_by: "sentence"`. A Layer 1.5 linear pre-filter (`app/triage.py`) clears
obviously-safe spans without a model call; those are marked `"by": "prefilter"` and can
never escalate. Spans with no grammatical substance (a row of logos, a catalogue
heading) are marked `low_syntax` and need a much higher score (`0.90`) to escalate,
because they score erratically.

**6. Word scan** — for each flagged span, which words drive the risk. Populates
`word_scores` and drives highlighting.

### Async mode

When `mode: async` (Celery + Redis), `POST /v1/moderate` returns `202` after the gate
runs, with:

```json
{ "request_id": "…", "status": "pending", "content_id": "LST-10293",
  "content_type": "product_listing", "status_url": "/v1/moderate/<request_id>",
  "moderation": { "enabled": true, "level": "strict", "…": "…" } }
```

A gate `block` still decides inline and returns `200` — a block needs no model.

## `GET /v1/moderate/<request_id>`

Fetch the result of an async submission. Returns the full result object once complete,
or `{"status": "pending"|"failed", …}`. In **sync mode this returns `404 not_supported`**,
because there is nothing to poll.

---

# 3. The moderation API — images, PDFs and video

## `POST /v1/moderate/media`

**`POST /v1/moderate/pdf` is gone.** It was an alias of this route and went with PDF
extraction; posting to it now gives `405`, because the URL matches
`GET /v1/moderate/{request_id}` and that rule takes no POST. Post files here.

**Of the three kinds, only an image is examined.** A PDF and a video are published
unexamined or refused, per the flags in 3.2 and 3.3 — see those sections before
reading an `allow` from this endpoint as "we checked it".

**Auth:** none. **Content-Type:** `multipart/form-data`.

### Request

| Field | Required | Notes |
|---|---|---|
| `file` | yes | **One** file per request — each gets its own decision and its own audit row. |
| `content_type` | no | Defaults to `article`. Same eleven values as the text endpoint. |
| `content_id` | no | Your platform's ID. |

**The file kind is detected from its bytes, never its filename** (`app/media.py`, `detect_kind`).

### The common shape

Whatever the kind, text that can be read out of the file is then passed through the
**identical** pipeline described in section 2 — so a brochure is judged by the same rules
as a pasted post. The response is the ordinary result object (section 4) plus a `media`
block. There is no longer a `pdf` block — it went with the extractor that filled it.

---

## 3.1 Images — two independent passes

An uploaded image gets looked at **twice**, because the two bypasses are different:

| Pass | Catches | Implementation |
|---|---|---|
| **OCR** | the scam put in a screenshot so no text field contains it | `app/ocr.py`, RapidOCR on onnxruntime |
| **Visual (CLIP)** | a weapon or gore photograph carrying no words at all | `app/clip.py`, CLIP ViT-B/32 against `config/visual_policy.yaml` |

### Order of operations

1. **Size.** Over `media.max_bytes` (**15 MB**) gives `413 media_too_large`.
2. **Decode and run both passes** (`read_image`). Decoded pixels are capped at
   `media.max_pixels` (**40,000,000**) against decompression bombs.
3. **Visual model threw** gives `503 media_not_checkable`. Refused, not allowed:
   publishing a picture nothing looked at is precisely the hole this check exists to
   close. The author did nothing wrong, so the message offers a retry.
4. **Visual check refuses the picture** gives `200` with a **reject**, shaped as a gate
   block (see below).
5. **OCR threw** gives `503 media_not_checkable`, same reasoning as step 3.
6. **There is writing and OCR could not read it** gives `422 media_unreadable`, unless
   rescued (see below).
7. **No text at all** gives `200 allow`. A photograph with no writing is ordinary, and
   the visual check already cleared it at step 4.
8. **Text was read** runs it through the text pipeline at a raised bar (see below).

### How the visual check decides

`VisualPolicy.decides_reject` evaluates these in order; the first that fires wins.

1. **Exemplar "allow"** at or above `exemplar_reject_min`, and at least as strong as any
   exemplar "block", means **publish**. An admin who pointed at a picture and said "allow
   things like this" is overruling a rule deliberately, so that override is checked first
   and **wins ties**.
2. **Exemplar "block"** at or above `exemplar_reject_min` means **refuse**.
3. **Trained probe** at or above `probe_reject_min` (**0.70**) means **refuse** — unless
   the category has been withdrawn, in which case the probe gets no vote.
4. **Each unsafe label at its own bar** (`visual.reject_min`, default **0.90**), plus the
   combined unsafe total, so a picture that is 0.5 weapon and 0.5 gore — obviously a
   problem, but confident in neither label — is still caught.

### What a visual refusal looks like

It is built as a **gate block**, so downstream it is indistinguishable from a blocked
phrase: same feedback wording, same audit row, same sink, same response shape your client
already handles. Because there is no text to quote, `content` is the filename —
`[flyer.png]`, so the author is told which upload was refused.

`rule_id` names **the channel that actually refused**, not the best-matching label:

| Cause | `rule_id` |
|---|---|
| exemplar | `visual.exemplar#<image id>` — the row to go and change |
| probe | `visual.probe.<label>` |
| written policy | `visual.<label>` |

The category reported to the author is the exemplar's **own** category, translated
through the policy's label-to-category map. This matters: before it was fixed, a cloud
refused by a rainbow exemplar was logged under `visual.safe` and the author was told it
broke the policy "on weapons" — the one place the cause was not.

### Text read off a picture is held to a higher bar

OCR of a shopfront comes back as word-salad, and the model — trained on prose — scores
that noise unreliably; it put four ordinary BONC listings in the reject band. So image
text is judged at `media.ocr_text_reject_min` (**0.99**) instead of the surface's level.

Measured: legitimate signage lands at 0.78 to 0.976, scam flyers at 0.9997, so the two
still separate cleanly.

`allow_max` and `reject_min` are set to **the same value**, giving one boundary and no
"revise" band. That is on purpose: "revise" asks the author to edit the highlighted
words, which is meaningless for words baked into a photograph. Their only remedies are to
replace the picture or leave it, so the verdict is binary.

**The gate is unaffected by this override.** A rule either matched or it did not, and a
scam phrase in a photograph is still a scam phrase.

### The unreadable-writing rescue

When OCR finds writing it cannot read, the words are unchecked — and answering "allow" is
how a Devanagari threat once published with a clean result. The default is therefore
`422 media_unreadable`.

Two settings widen that:

- `media.allow_unreadable_image` (default **false**) — always allow.
- `media.allow_unreadable_image_when_seen` (default **true**) — allow when the visual
  check ran and cleared the picture. Refusing on unreadable writing alone was turning
  away **13% of real listings** over logos, number plates and watermarks.

A rescued image is allowed with `checked: false` in its media block. That is a narrower
claim — "we looked at it, we could not read the writing" — and never a clean result.

---

## 3.2 PDFs — not checked, and allowed anyway

**Nothing reads a PDF.** `app/pdf.py` — extraction, OCR of scanned pages, and the
per-page visual check — was removed while the scanned path is reworked. A PDF is now
treated exactly as a video is, with the opposite default:

| `media.allow_unchecked_pdf` | Behaviour |
|---|---|
| `true` (**code and `settings.yaml` default**) | Allow, and say so: `checked: false`, `visual_content_checked: false`, `text_found: false`. |
| `false` | `422 media_not_checkable` — refuse it. |

**This is fail-open and deliberate.** A scam brochure or a scanned threat publishes with
nobody having read it. `checked: false` is the whole of what protects you: an `allow` on
a PDF is not a clean result, and a client that treats it as one is wrong. Set the flag to
`false` the day a document must not go out unread.

### Why it was stopped

Not because extraction was weak — it held up well. Of 29 live checks over the PDF path, 27
passed: page-level offsets, paragraph unwrapping across PDF line breaks, OCR of scanned
pages, encryption, damage and all four limits. What stopped it was the **scanned** path,
where two defects meet:

1. **Every visual refusal on a PDF page was logged against the wrong rule.** The PDF
   branch passed an explicit `rule_id` built from the best-matching *label*, which skipped
   the cause-naming the image path uses (`refused_by()`, guarded by `if not rule_id:`). A
   page refused by exemplar #3672 was recorded as **`visual.safe`** — a rule that does not
   exist, naming the safe label as the reason. The comment directly above that helper
   describes this exact bug being fixed for images.

2. **That exemplar matches flat, document-like pages.** Eight ordinary business documents
   rendered as scans — a letter, a price list, a spec sheet, a supplier profile — and
   **six were refused**, the worst being a *blank page* at 0.6945 against a 0.60 bar. For
   contrast, only 2 of 30 real photographs from the dataset cross it. So this is not the
   exemplar mechanism misfiring generally; it is specific to the flat, low-detail images
   that every page of a scan produces.

Together: a supplier scans their profile, OCR reads it perfectly, the words are clean, and
it is refused for a policy violation whose audit row points at the wrong rule.

Picking it back up means fixing (1) — carry the refusal cause through `PageVisual` and drop
that argument — and a policy decision on (2) for whoever owns `visual_policy.yaml`. The
removed module, its tests and the live checks that found this are in
`private_notes/pdf_on_hold/` and in the history of the commit that removed them.

---

## 3.3 Video — not checked

**No frame decoding, no audio analysis.** The audio-transcription path and its Whisper
dependency were removed. A video's title and description go through `POST /v1/moderate`
as `content_type: "video"` like any other text; the footage itself is never examined.

`media.allow_unchecked_video` decides what happens to an uploaded video file:

| Value | Behaviour |
|---|---|
| `false` (**code default**) | `422 media_not_checkable` — refuse it. |
| `true` (**current `settings.yaml`**) | Allow, and say so: `checked: false`, `visual_content_checked: false`. |

The current platform policy is to publish unwatched footage rather than close the Videos
tab, with the risk stated in the record rather than passed off as clean. Video remains
covered by human reporting, not by this service. Set the flag back to `false` the day
footage must not go out unwatched.

Anything that is not an image, a PDF or a video gives `422 media_unsupported`.

---

# 4. The result payload, field by field

Produced by `app/pipeline.py`, `Pipeline._build`. The same object is returned to the
caller and handed to the sink for the audit log. Contract notes live in
[contracts/moderation_result.md](../contracts/moderation_result.md).

```json
{
  "schema_version": "1.9",
  "request_id": "f1e2d3c4-…",
  "status": "completed",
  "content_id": "LST-10293",
  "content_type": "product_listing",
  "content": "Premium basmati rice exporter, FSSAI certified.",
  "content_sha256": "9f2b…",
  "decision": "allow",
  "decided_by": "model",
  "risk_score": 0.0004,
  "predicted_label": "safe",
  "label_scores": { "safe": 0.9996, "unsafe": 0.0004 },
  "gate_matches": [],
  "targeted_segments": [],
  "sentence_scores": [],
  "word_scores": [],
  "feedback": null,
  "gate_version": 2,
  "model_version": "v8",
  "thresholds": { "allow_max": 0.30, "reject_min": 0.70 },
  "moderation": { "enabled": true, "level": "strict", "reject_min": 0.25 },
  "latency_ms": { "gate": 0.12, "inference": 17.4, "total": 18.1, "stages": {} },
  "decided_at": "2026-10-31T09:14:22.113402+00:00"
}
```

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | string | Currently `"1.9"`. Bumped when the shape changes (1.9 added the grievance stage; 1.8 per-feature levels; 1.3 sentence scores). Branch on it rather than sniffing fields. |
| `request_id` | string | UUID. The key for `GET /v1/moderate/<id>` and for the audit log. |
| `status` | string | `"completed"`, or `"pending"` on a 202. |
| `content_id` | string \| null | Your ID, echoed back as a string. |
| `content_type` | enum | The surface, echoed back. |
| `content` | string | The text that was judged. For media, the filename in brackets. |
| `content_sha256` | string | Hash of `content`, for deduplication and tamper-checking. |
| `decision` | enum | `allow`, `revise` or `reject`. **This is the field you act on.** |
| `decided_by` | enum | Which stage decided: `gate`, `model`, `targeted`, `sentence`, `disabled`, `grievance`. |
| `risk_score` | float \| null | 0 to 1, calibrated. **Null** after a gate block or when nothing ran. |
| `predicted_label` | string \| null | The winning label. |
| `label_scores` | object \| null | Every label's score, rounded to 6 places. |
| `gate_matches` | array | `{rule_id, category, action, spans}` for each Layer 1 rule that matched. |
| `targeted_segments` | array | Sentences aimed at someone: `{start, end, risk_score, predicted_label}`. |
| `sentence_scores` | array | Per-sentence `{start, end, risk_score}`, plus `kind: "window"` for word windows, `low_syntax: true` for spans with no grammatical substance, `by: "prefilter"` for spans cleared without a model call. |
| `word_scores` | array | Per-word contribution within flagged spans. |
| `feedback` | object \| null | Author-facing. **Null when allowed.** See below. |
| `gate_version` | string | Version of `gate_patterns.yaml` in force. |
| `model_version` | string \| null | The ONNX bundle that scored it. |
| `thresholds` | object | The `allow_max` / `reject_min` actually applied to this request. |
| `moderation` | object | The admin's policy for this surface: `enabled`, `level`, the bar. |
| `latency_ms` | object | `gate`, `inference`, `total`, and a per-stage `stages` breakdown. |
| `decided_at` | string | UTC ISO-8601. |

### Offsets, not copies

`targeted_segments`, `sentence_scores`, `word_scores` and `feedback.issues` all carry
**character offsets into `content`**, never copies of the text. Audit rows therefore stay
free of duplicated content, and the client can highlight precisely.

### The `feedback` object

Null on allow. Otherwise:

```json
{
  "title": "Your listing needs a few changes",
  "message": "This cannot be published as written: it breaks our policies on scams and payment fraud.",
  "severity": "high",
  "categories": ["scam", "payment_fraud"],
  "issues": [
    { "start": 142, "end": 207, "source": "model",
      "message": "This sentence reads as a scam.", "risk_score": 0.9997,
      "words": [[150, 158], [171, 177]] }
  ]
}
```

Each issue carries `start`, `end`, `source` (`"rule"` or `"model"`) and `message`.
`rule_id`, `category`, `risk_score` and `words` are included only when they apply —
`words` gives the offsets of the trigger words inside the span, for word-level
highlighting.

`severity` is `high` at a risk of 0.80 or above, `medium` from 0.50, `low` below that.
A gate block carries no model score at all and is reported `high`; a gate `revise` rule
on text the model was happy with is reported `medium`, since the model's own score would
read as `low` and understate it.

- `title` and `message` are already phrased for the surface — "Your **proposal** needs a
  few changes" — using the noun map in `config/feedback_messages.yaml`. Show them as-is.
- `categories` names the policy areas breached. **A blank category means "do not name
  one"**, and the generic message is used instead; this is how an exemplar refusal whose
  picture sits in a folder that names no policy avoids inventing an area.
- `issues` is sorted by position and non-overlapping, ready to highlight.
- On `reject`, `issues` is populated only when `feedback.highlight_on_reject` is true.

### What `decided_by: "disabled"` means

`decision: "allow"` with `decided_by: "disabled"` and a null `risk_score` means **nothing
ran** — moderation is switched off for that surface. It must never be read as "checked
and found clean". The same distinction is what `visual_content_checked` draws for
pictures.

### The `media` block

Present on `/v1/moderate/media` responses.

| Field | Meaning |
|---|---|
| `filename` | As uploaded. |
| `kind` | `image`, `pdf` or `video`, detected from the bytes. |
| `text_found` | Whether OCR found any writing. |
| `text_readable` | Whether it could actually read it. |
| `ocr_confidence` | 0 to 1. |
| `visual_content_checked` | **Whether the picture itself was scored.** False means nobody looked. |
| `checked` | False when the file was published without being fully examined. |
| `visual` | The visual check's scores and winning label. |

> **`visual_content_checked: false` is not a clean result.** It means the visual model was
> off, missing, or could not run. Treat it as "unexamined", and decide by policy whether
> unexamined media may publish.

---

# 5. Operational endpoints

## `GET /health`

**Auth:** none. Always `200 {"status": "ok"}`. Pure liveness — it touches nothing, so it
stays up while the model is broken. Use it for the load balancer's "is the process alive"
check.

## `GET /ready`

**Auth:** none. Readiness, and the single most useful diagnostic in the service.

```json
{
  "status": "ready",
  "mode": "sync",
  "gate_version": 2,
  "gate_rules": 184,
  "thresholds": { "allow_max": 0.30, "reject_min": 0.70 },
  "visual": {
    "enabled": true, "ready": true,
    "labels": ["gore", "minor", "safe", "weapons", "…"],
    "prompts": 15, "reject_min": 0.90, "probe": true, "probe_reject_min": 0.70,
    "provider": "CPUExecutionProvider", "scoring": "softmax",
    "label_thresholds": { "gore": 0.90, "weapons": 0.90 }
  },
  "moderation_disabled_for": [],
  "model_ready": true,
  "model_version": "v8",
  "model_backend": "onnx",
  "model_error": null
}
```

Returns **`503` with `"status": "not_ready"`** when the mode is `sync` and the model is
not loaded. The body is identical either way, so a failing probe still explains itself via
`model_error`.

Two fields exist specifically so an operator can see things from outside rather than infer
them from published content:

- **`visual`** — whether pictures are actually being looked at. The visual check turns
  itself off when its model is missing, and that is exactly when you want to know. When it
  is not ready the block collapses to `{enabled, ready: false, reason}`, naming what is
  missing.
- **`moderation_disabled_for`** — which surfaces are currently unmoderated.

### Startup behaviour

If the model fails to load at boot, the process **keeps serving**: `/ready` and
`/v1/moderate` return `503` until an admin reload succeeds, rather than crash-looping.

## `GET /demo`

The browser demo UI. **Enabled only with `DEMO_PAGE=1`; never on in production.**
Returns `404 not_found` otherwise.

## `GET /articles` and `GET /articles/<path:name>`

The reference front-end: a live tab bar, a per-surface form, and the Write-article modal
with publish-time moderation. Also gated behind `DEMO_PAGE=1`.

Worth reading before writing your own client — `moderation-client.js` here is the
reference implementation of field joining and offset-based highlighting.

## `GET /docs` and `GET /openapi.yaml`

Swagger UI and the OpenAPI document behind it. **These are not demo routes and are not
gated behind `DEMO_PAGE`** — they are served by default, and `API_DOCS=0` turns them off.

The reasoning: `/demo` and `/articles` are a sales aid and a front-end example, and no
integration depends on them, so they stay off unless somebody asks. The contract is the
opposite. Teams calling this service have to code against the request and response
shapes, and a running instance is the only copy of those shapes that cannot be out of
date — a checked-in file can lag a deploy, the live service never does. Both routes
publish the *shape* of the endpoints: no data, no credentials, no configuration values.

Turn them off with `API_DOCS=0` where the endpoint list itself should not be public —
typically an instance reachable from the open internet rather than one behind your own
network. `/docs` then answers `404 not_found` with `"API docs are turned off
(API_DOCS=0)"`.

`/docs` deliberately serves Swagger UI **from this app's own origin**, so "Try it out"
is a same-origin request. This service sends no CORS headers anywhere; reaching a
Swagger UI hosted elsewhere would mean relaxing that on every endpoint, the
unauthenticated submission endpoints included, for a developer convenience.

`/openapi.yaml` is read from the repository's `docs/` directory, where the spec lives so
it sits beside this file and cannot drift from a second copy. A deployment that ships
`flask_api/` alone carries no `docs/`, and the route says so rather than returning a
bare `404`.

---

# 6. Admin — authentication

`app/admin_auth.py`. Session cookies are Flask's signed, permanent sessions carrying the
admin id, a `session_version` (bumped to invalidate every existing session) and a
timestamp.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/admin/auth/login` | Sign in, set the session cookie |
| `POST` | `/v1/admin/auth/logout` | Clear the session |
| `GET` | `/v1/admin/auth/me` | `{email}` for the current session, else `401` |
| `POST` | `/v1/admin/auth/password/forgot` | Request a 6-digit code by email |
| `POST` | `/v1/admin/auth/password/verify` | Exchange the code for a reset token |
| `POST` | `/v1/admin/auth/password/reset` | Set the new password |

## `POST /v1/admin/auth/login`

```json
{ "email": "admin@example.com", "password": "…" }
```

Returns `200 {"email": "…"}` and sets the cookie. `401 invalid_credentials` on failure —
the same message whether the account exists or not, and a dummy hash is checked when it
does not, so response timing does not reveal which.

**Rate limited:** 20 attempts per IP and 5 per email address, each per 15 minutes.
Exceeding either gives `429 rate_limited` with a `Retry-After` header.

## The password reset flow

Three steps, and each resists a different attack.

1. **`password/forgot`** — `{email}`. **Always returns the same `200` body**, whether or
   not the address belongs to an admin, so it cannot be used to enumerate accounts. A
   limit being hit returns that same body too. A 6-digit code is hashed with the app
   secret before storage, expires after `OTP_TTL`, replaces any older code, and is subject
   to a resend cooldown. It is delivered on a background thread, and **never written to
   the log**. If no mailer is configured the failure is logged and nothing is sent.
   *Limits: 10 per IP and 5 per email, per hour.*

2. **`password/verify`** — `{email, code}`. Returns `200 {"reset_token": "…"}`.
   The code is checked against a maximum attempt count and the token is stored hashed.
   Failures distinguish `expired`, `locked` (too many wrong codes) and plain wrong, but
   all return `400 invalid_code`. *Limit: 30 per IP per 15 minutes.*

3. **`password/reset`** — `{email, reset_token, new_password}`. Validates the password
   (at least `MIN_PASSWORD` characters, must mix letters and digits, must not be the email
   address, at most 200 characters) and **clears the session** on success, so an attacker
   holding a stolen cookie loses it. *Limit: 20 per IP per 15 minutes.*

> **Note.** Rate limiting exists only on these five auth endpoints. There is none on
> `/v1/moderate`. See section 12.

---

# 7. Admin — moderation levels and settings

Two overlapping APIs control how strictly each surface is moderated. **They disagree
about granularity — read the warning at the end of this section before using either.**

## The model

Each surface has a `FeaturePolicy`: `enabled` (is it moderated at all) and a bar
expressed either as a named `level` or as a raw `reject_min` / `allow_max`. A raw number
always beats a named level, so the dashboard can offer three buttons while an expert
types `0.62`.

Named levels are defined in `config/moderation_levels.yaml`:

| Level | `reject_min` | Meaning |
|---|---|---|
| `strict` | 0.25 | Nothing contentious. Complaints, outcry and callouts all refused. |
| `medium` | 0.50 | The model's own calibration point. |
| `lenient` | 0.85 | Only the clearly offensive or fraudulent is refused. |
| `level_1` … `level_10` | 0.10 … 1.00 | The 10-step "heat bar", strictly cumulative. |

Where `allow_max` equals `reject_min` there is a single boundary and **no revise band**.
Setting `allow_max` lower restores the middle band, where content is returned to the
author to revise rather than refused.

Changes made through these endpoints take effect on the **next request** — nothing
restarts — and are persisted to the admin database so a restart keeps them.

## Granular: one surface at a time

### `GET /v1/admin/moderation-levels`

The whole table: every named level, the default policy, and each configured surface.

### `PUT /v1/admin/moderation-levels/<content_type>`

```json
{ "enabled": false }        // the toggle
{ "level": "strict" }       // a named level
{ "reject_min": 0.62 }      // the rating directly, 0 to 1
```

Accepts any of `enabled`, `level`, `reject_min`, `allow_max`. Unknown fields give
`400 invalid_request` listing what was expected.

`enabled` and the bar are **independent**: setting a level on a disabled surface stores
it for when the toggle comes back on.

**A surface that does not exist is a `404 no_such_surface`, not a `400`** — the
difference between "there is no such page" and "that value is wrong". An admin who typed
`articles` (the plural) needs the first.

### `DELETE /v1/admin/moderation-levels/<content_type>`

Drops the surface's override so it follows the default again.

**This is not the same as setting it back to the default's current value**, which freezes
a copy that stops tracking later changes to the default. An admin undoing an experiment
means "follow the default", and this is the only way to express it.

Returns `had_override` so the caller can tell whether anything was actually cleared.

## Coarse: the dashboard's single control

### `GET /v1/admin/moderation-settings`

Returns a flattened view for the dashboard:

```json
{ "enabled": true, "level": "strict", "mixed": false,
  "levels": ["lenient", "level_1", "…"], "surfaces": { "article": {…}, "…": {} } }
```

`level` is non-null **only when every surface agrees**. `mixed` is true when they do not.

### `PUT /v1/admin/moderation-settings`

```json
{ "enabled": true, "level": "strict" }
```

Both fields are required and nothing else is accepted. **This applies the one setting to
every surface in a loop.**

Writes are serialised behind a lock so two admins saving at once cannot interleave, and
if the database write fails the in-memory levels are **rolled back** so memory and disk
keep telling the same story — the call then returns `500 save_failed` and nothing has
changed.

> ### Warning: these two APIs collide
>
> `PUT /v1/admin/moderation-settings` overwrites **every** surface. Any per-surface
> tuning done through `PUT /v1/admin/moderation-levels/<content_type>` is silently
> flattened by the next save from the dashboard's simple control.
>
> This matters because per-surface levels are the documented intent — the commented-out
> table in `moderation_levels.yaml` sets `article` and `post` to `strict`,
> `business_profile` to `medium`, `proposal` to `lenient`, and explains why each. The
> `mixed` flag in the `GET` exists so a UI can warn before overwriting; whether it does is
> a front-end question.
>
> Note also that `features:` in `moderation_levels.yaml` currently ships as `{}` — no
> surface has an override, so every one of them falls through to the single default.

## `POST /v1/admin/model/reload`

**Auth: `X-Admin-Token` only.** A logged-in session is *not* accepted here, unlike every
other admin endpoint. Returns `404 not_found` when `ADMIN_TOKEN` is unset.

Hot-swaps to the bundle in `MODEL_DIR` — for example after the ML team ships a new
version. **It only swaps if the new model loads and warms up**; otherwise the old one
stays live and the call returns `500 reload_failed` with `active_version` naming what is
still serving.

```json
{ "status": "reloaded", "previous_version": "v7", "model_version": "v8" }
```

---

# 8. Admin — audit log

Every decision is written to the admin database by `AdminStoreSink`, chained onto
whatever primary sink is configured. Chaining rather than replacing means the primary
sink keeps emitting whether or not the audit write succeeds.

## `GET /v1/admin/moderation-logs`

Query parameters: `decision`, `content_type`, `severity`, `q` (free-text search),
`limit` (default 50, **capped at 200**), `offset` (default 0). Malformed numbers fall
back to the defaults rather than erroring.

Returns `{logs, total, limit, offset, stats}`. **The array is `logs`, not `items`** — the
image listing uses `items`, and the two are not interchangeable. `total` counts the rows
matching the filters, not the rows in this page, and `stats` carries `allow` / `revise` /
`reject` / `total` over that same filtered set so the header tiles need no second request.

## `GET /v1/admin/moderation-logs/<request_id>`

One decision in full. `404 not_found` if there is no such request.

## `GET /v1/admin/moderation-logs/export`

Download the log as a file. Takes **the same filter parameters** as the list endpoint and
applies them through the same builder, because the button sits on a filtered screen and
"export" there can only sensibly mean "what I am looking at".

| `format` | Accepted aliases | Output |
|---|---|---|
| `txt` | — | plain text (default) |
| `doc` | `word`, `docx` | Word |
| `xlsx` | `excel`, `xls` | Excel |

Anything else gives `400 bad_format`.

**It is not paginated.** The screen shows fifteen rows and nobody wants fifteen rows in a
spreadsheet, so this walks the whole matching set up to `max_rows` (capped at
`MAX_EXPORT_ROWS`) and the file records both numbers about itself.

Response headers:

| Header | Meaning |
|---|---|
| `Content-Disposition` | `attachment; filename="…"` |
| `X-Export-Rows` | rows actually written |
| `X-Export-Total` | rows that matched the filter |
| `Cache-Control` | `no-store` — an audit export is a point-in-time copy of a table still being written to |

Only the Excel path has an optional dependency. If `openpyxl` is missing it returns
`503 export_unavailable` naming the missing package; **the text and Word exports still
work**, so the button is not broken for everyone by one absent library.

## `DELETE /v1/admin/moderation-logs`

Clears the entire audit log and returns the number of rows removed. There is no
confirmation step and no filter — it takes everything.

---

# 9. Admin — blocked clients

**What this is and is not.** Blocking an address stops casual repetition from one
connection — the same person posting the same thing eight times — and that is worth
having. It is **not an identity control**: a mobile connection changes address by
switching to cellular, and a VPN changes it by clicking.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/admin/blocked-clients` | List current blocks |
| `POST` | `/v1/admin/blocked-clients` | Add one |
| `DELETE` | `/v1/admin/blocked-clients/<value>?scope=` | Remove one |

## `POST /v1/admin/blocked-clients`

```json
{ "value": "203.0.113.45", "scope": "ip", "reason": "repeat scam listings" }
```

`scope` is `ip` or `network`. A network is normalised into the same form the resolver
produces, or the two would never compare equal. The `reason` and the acting admin's email
are recorded, because the person undoing this months later will not be the one who did
it. A script authenticating by token has no session row, so the actor is recorded as
blank rather than failing the call.

Returns `201` with the stored row.

| Error | When |
|---|---|
| `400 missing_value` | no address given |
| `400 bad_scope` | scope is neither `ip` nor `network` |
| `400 bad_address` | not a valid address or network — caught here so a typo becomes a message rather than a row that silently blocks nothing |
| `409 would_block_self` | **the address is your own connection** |

That last one is the important guard: blocking your own address would stop your own
submissions, and the screen that would undo it sits behind the block. The error names the
address it detected so you can check it before going ahead.

> **A `/24` can be one household or one office behind a single NAT.** Refusing a network
> can refuse people who did nothing. Prefer `ip` scope unless you have a reason.

## `DELETE /v1/admin/blocked-clients/<value>`

Optional `?scope=` narrows it. Returns the number of rows removed, or
`404 not_blocked`.

---

# 10. Admin — image dataset and training

`app/admin_images.py`. The dataset behind the visual check: the pictures, what each is
filed under, and the trained probe built from them. All endpoints require admin auth.

## Categories

| Method | Path | Notes |
|---|---|---|
| `GET` | `/v1/admin/images/categories` | Also returns `active_mode` (e.g. `standard`, `child_products`) |
| `POST` | `/v1/admin/images/categories` | `{name, display_name?, default_action?, description?}` → `201`, or `409 category_exists` |
| `PUT` | `/v1/admin/images/categories/<name>` | `404 not_found` if absent |

`default_action` is `allow` or `block`.

## Listing and files

| Method | Path | Notes |
|---|---|---|
| `GET` | `/v1/admin/images` | Filters: `category`, `action`, `selected` (`0`/`1`), `search`; paging: `page` (1-based), `limit` (default 24) |
| `GET` | `/v1/admin/images/<id>` | One record |
| `GET` | `/v1/admin/images/<id>/file` | The bytes, cached an hour. `404 file_missing` if the row exists but the file does not |

Returns `{items, pagination, filtered, stats, active_policy_mode, withdrawn_categories}`.
**The array is `items`, and there is no top-level `total`** — it is `pagination.total`,
beside `page`, `limit` and `total_pages`. `filtered` counts the current filter's matches
and `stats` the whole dataset, so the two differ whenever a filter is applied.

The listing also returns `withdrawn_categories`. The grid needs it because an exemplar in
a withdrawn category still renders as a live rule — "refusing lookalikes" — while the
live check is ignoring it, which would be the card stating the opposite of the truth.

## `POST /v1/admin/images/upload`

`multipart/form-data`: `file` (required), `category` (default `safe`), `action`
(`allow` or `block`, default `allow`), `description`.

Returns `201` with the stored record and a `taught` block saying what the upload changed:
whether the description was added as a policy phrasing, and that the image is selected
for the next retrain.

## `PUT /v1/admin/images/batch`

Three different operations behind one route, chosen by which key is present.

**1. `select_all`** — bulk select or deselect. Takes the **same filters the grid is
showing** (`category`, `search`, `action`), not just the category. Taking only the
category made the button act on pictures the admin could not see: searching "gym" shows
64 images across two categories, and Deselect All deselected a whole category instead —
and the retrain that followed was measuring a change nobody had made.

The response message names **what** was touched, not just how many: "Deselected 72 images
in category physique" rather than "Deselected 72 images". The two read the same until the
sets differ, which is exactly when the admin needs to be told.

**2. `bulk_action` + `category`** — set every image in a category to allow or block.

**3. `updates`** — an array of per-image modifications. Must be an array or
`400 invalid_updates`.

Only `action` and `category` reach the live check; `is_selected` is the next retrain's
business and deliberately does **not** trigger a reload. Exemplars touched by the change
are resynchronised and the response reports how many rules are now refusing and allowing.

## Training

| Method | Path | Notes |
|---|---|---|
| `POST` | `/v1/admin/images/train` | `{C?, preset?}` — retrains the probe. `500 training_error` on failure |
| `POST` | `/v1/admin/images/preset` | `{preset}` e.g. `child_products`, `standard`. `400 missing_preset` |
| `GET` | `/v1/admin/images/history` | Last 10 training runs plus `active_policy_mode` |
| `POST` | `/v1/admin/images/load-all` | Bulk-import the dataset directory into the database |

---

# 11. Admin — visual policy

`app/admin_policy.py`. **The policy for pictures is written in words**, in
`config/visual_policy.yaml`: each label carries phrasings that CLIP scores an image
against, and a verdict. Adding a category needs no retraining.

A pristine copy of the policy is snapshotted at startup, before any admin can edit it, so
"reset to how it shipped" always means what it says rather than whatever the first editor
happened to save.

## `GET /v1/admin/policy/labels`

Returns the labels, the file `version`, and four things that exist to stop the editor
lying to its user:

| Field | Why it is there |
|---|---|
| `live_labels` | What the **model** is scoring right now — that is `prompts.npz`, not this file |
| `in_sync` | Whether the two agree. They differ when someone edited the YAML by hand without rebuilding |
| `default_available` | Whether a pristine copy exists to reset to |
| `named_categories` | Categories that have a reader-facing name, so the editor can offer them |
| `balance` | How the refusing and allowing phrasings are distributed |

`named_categories` matters: letting the category field default to the rule name is how
`abuses` came to be minted next to the `abuse` that already meant the same thing, with
nothing on screen saying the two were different.

## `PUT /v1/admin/policy/labels/<name>`

Create or replace a rule. Returns `created` or `updated`, the stored entry, the resulting
`live_labels`, and a **`consequences`** block saying what the save means for the author
and for the rest of the policy — decided here and reported nowhere else.

### Conflict detection

Refusing and allowing the same sentence are both coherent policies, and only the admin
knows which they meant — so a clash **stops and asks** rather than letting write order
decide. A conflicting save returns:

```
409 policy_conflict
  conflicts: [ … the clashing rules and phrasings … ]
  this_verdict: "refuse" | "allow"
```

Resend with `"resolve": "refuse"` or `"resolve": "allow"` to say which side stands. If
the *existing* rule wins, the clashing phrasings are dropped from the one being saved —
and if that would leave it with no phrasings at all, the call returns `409` again rather
than storing an empty rule.

**Only what the edit introduces is checked.** Checking every wording on every save meant
four of the fifteen shipped rules could not be edited at all: they overlap `safe` closely
enough to trip the similarity bar, they have shipped that way from the start, and
changing one rule's `reject_min` asked the admin to settle a contradiction they had not
made and could only clear by deleting a wording they had not written. A contradiction
already in the file is not news; an edit that *creates* one is — which means a phrasing
being added, or a verdict being flipped. A flip changes the meaning of every wording the
rule holds, so that case rechecks them all.

## `POST /v1/admin/policy/labels/<name>/phrasings`

Add **one** phrasing: `{phrasing, unsafe?, category?, resolve?}`.

Separate from the `PUT` because that replaces a rule's whole prompt list, and the two
callers that need this — the upload form and the conflict dialog it raises — are adding a
single sentence to whatever is already there. Going through `PUT` would mean the client
reconstructing the existing wordings and sending them back, which is a race with anyone
else editing and a way to lose phrasings by accident.

Same `409 policy_conflict` contract as above.

## `DELETE /v1/admin/policy/labels/<name>`

`404 no_such_label` if absent. **Removing the last allowing rule is refused**: the safe
phrasings are what the unsafe ones are judged against, so deleting the last one would
make every ordinary photograph compete only against rules that refuse it.

## Exemplars — "refuse things like this"

| Method | Path |
|---|---|
| `GET` | `/v1/admin/policy/exemplars` |
| `PUT` | `/v1/admin/policy/exemplars` |

This is the half of policy an admin actually has. Writing phrasings is a skill; pointing
at a picture they just looked at is not, and it is how the request arrives — somebody
uploads an image and wants it refused, or changes their mind about one being refused now.

`PUT` takes `{ids: [...], mode: "block" | "allow" | "clear", preview?, resolve?}`.
It takes effect **on the next upload**: no retraining, no dataset, and undoing it is
clearing the same flag.

| Error | When |
|---|---|
| `400 missing_ids` | the list is empty or not an array |
| `400 bad_ids` | an id is not a number |
| `400 bad_mode` | mode is not `block`, `allow` or `clear` |
| `404 no_such_image` | none of the ids are stored (preview only) |
| `409 no_embeddings` | those pictures have no embedding to compare (preview only) |

### `preview: true` — measure the reach before marking

**An exemplar generalises by appearance, and appearance does not respect the categories a
person has in mind.** A rainbow marked "refuse things like this" scored 0.828 against a
cloud photograph — both are soft-coloured sky — and the cloud was refused with nothing in
the policy naming clouds. Deselecting it and retraining the probe could not help, because
the probe was not the one objecting.

So `preview` measures the reach and shows it **before** the mark is applied, without
changing anything:

```json
{ "preview": true, "would_match": 37,
  "by_category": { "safe": 21, "cloud": 9, "physique": 7 },
  "top": [ … the twelve closest, most similar first … ] }
```

Grouped by the category the matched pictures are filed under, because the surprise is
never "it caught more" but "it caught **those**".

Exemplars live in a file beside `prompts.npz`, so the visual check picks them up without
knowing the admin database exists.

## `POST /v1/admin/policy/preview`

Score candidate phrasings against the stored images **without changing anything**.

| Field | Required | Notes |
|---|---|---|
| `prompts` | yes | 1–24 phrasings, 200 characters each. **The key is `prompts`, not `phrasings`** — anything else is `400 missing_prompts`. |
| `limit` | no | How many pictures to return, 1–48, default 12 |
| `selected_only` | no | Score only the images selected for the next retrain |
| `over_threshold` | no | Add the `over_threshold` flag per picture — read the warning below first |

Returns `{prompts, scored, top, over_threshold, note}`.

This is what makes writing policy in words usable rather than a guess. Until it existed,
the only way to find out whether "a photo of fireworks exploding" caught what you meant
was to apply it and watch the moderation queue. It scores against embeddings already in
`system.db`, so there is no inference over images at all — one subprocess to encode the
phrasings, then a dot product.

> ### These numbers are not the live scores
>
> They are **raw cosine similarities**, which for CLIP sit in a narrow band: measured, a
> wedding photograph against "a photo of a wedding ceremony" is **0.32** here while the
> same picture scores **0.96** through the live `classify()`. The live check softmaxes
> across every label, which concentrates almost all the mass on the winner; a raw
> similarity does not.
>
> So `over_threshold` is returned only when the caller asks for it, and this must be
> presented as a **ranking** — which of your pictures these words pick out — never as a
> prediction of `reject_min`.
>
> Ranking is the right question anyway. A wording is good when it *separates*: "a photo
> of a wedding ceremony" puts five wedding photographs at the top, while "a photo of
> fireworks exploding" returns gore, romance and a safe listing at nearly identical
> scores — which is exactly how it looks when nothing in the set matches. That contrast
> is visible in the order and invisible in any single number.

## `POST /v1/admin/policy/reset`

Back to the policy as it shipped, from the startup snapshot. `409 no_default` when
nothing has been edited yet, so there is nothing to go back to.

---

# 12. Appendix

## 12.1 Complete endpoint index

**Moderation** (`app/api.py`, blueprint `moderation`)

| Method | Path | Auth |
|---|---|---|
| `POST` | `/v1/moderate` | none |
| `POST` | `/v1/moderate/media` | none |
| `GET` | `/v1/moderate/<request_id>` | none |
| `GET` | `/health` | none |
| `GET` | `/ready` | none |
| `GET` | `/demo` | none, `DEMO_PAGE=1` |
| `GET` | `/articles`, `/articles/<path:name>` | none, `DEMO_PAGE=1` |
| `GET` | `/docs` — Swagger UI, same origin | none, on unless `API_DOCS=0` |
| `GET` | `/openapi.yaml` — the spec | none, on unless `API_DOCS=0` |
| `GET` | `/v1/admin/moderation-levels` | admin |
| `PUT` | `/v1/admin/moderation-levels/<content_type>` | admin |
| `DELETE` | `/v1/admin/moderation-levels/<content_type>` | admin |
| `POST` | `/v1/admin/model/reload` | **token only** |

**Admin auth and audit** (`app/admin_auth.py`, blueprint `admin_auth`)

| Method | Path |
|---|---|
| `POST` | `/v1/admin/auth/login` |
| `POST` | `/v1/admin/auth/logout` |
| `GET` | `/v1/admin/auth/me` |
| `POST` | `/v1/admin/auth/password/forgot` |
| `POST` | `/v1/admin/auth/password/verify` |
| `POST` | `/v1/admin/auth/password/reset` |
| `GET` `PUT` | `/v1/admin/moderation-settings` |
| `GET` | `/v1/admin/moderation-logs` |
| `GET` | `/v1/admin/moderation-logs/export` |
| `GET` | `/v1/admin/moderation-logs/<request_id>` |
| `DELETE` | `/v1/admin/moderation-logs` |
| `GET` `POST` | `/v1/admin/blocked-clients` |
| `DELETE` | `/v1/admin/blocked-clients/<path:value>` |

**Admin images** (`app/admin_images.py`, blueprint `admin_images`)

| Method | Path |
|---|---|
| `GET` `POST` | `/v1/admin/images/categories` |
| `PUT` | `/v1/admin/images/categories/<name>` |
| `GET` | `/v1/admin/images` |
| `GET` | `/v1/admin/images/<int:image_id>` |
| `GET` | `/v1/admin/images/<int:image_id>/file` |
| `POST` | `/v1/admin/images/upload` |
| `PUT` | `/v1/admin/images/batch` |
| `POST` | `/v1/admin/images/train` |
| `POST` | `/v1/admin/images/preset` |
| `GET` | `/v1/admin/images/history` |
| `POST` | `/v1/admin/images/load-all` |

**Admin visual policy** (`app/admin_policy.py`, blueprint `admin_policy`)

| Method | Path |
|---|---|
| `GET` | `/v1/admin/policy/labels` |
| `PUT` `DELETE` | `/v1/admin/policy/labels/<name>` |
| `POST` | `/v1/admin/policy/labels/<name>/phrasings` |
| `GET` `PUT` | `/v1/admin/policy/exemplars` |
| `POST` | `/v1/admin/policy/preview` |
| `POST` | `/v1/admin/policy/reset` |

## 12.2 Enums

**`content_type`** — eleven values, defined in `app/pipeline.py`, `ContentType`:

`business_profile`, `product_listing`, `post`, `group_post`, `advertisement`, `article`,
`network_article`, `video`, `request`, `proposal`, `business_proposal`

`group_post` and `network_article` exist because the platform posts and publishes articles
from two places each, and an admin needs to be able to moderate a group post differently
from an open-feed post. **Both are public surfaces that the grievance check does not yet
cover** — see 12.5.

**`decision`** — `allow`, `revise`, `reject`.
`revise` means not published yet: the author is shown what to fix and can resubmit.

**`decided_by`** (`Stage`) — `gate`, `model`, `targeted`, `sentence`, `disabled`,
`grievance`.

**Gate rule actions** — `block`, `revise`, `flag`.

**`severity`** — `high` (risk ≥ 0.80), `medium` (≥ 0.50), `low`.

## 12.3 Limits and defaults

| Setting | Default | Applies to |
|---|---|---|
| `limits.max_chars` | 25,000 | `content` on `/v1/moderate` |
| `thresholds.allow_max` | 0.30 | the default bar |
| `thresholds.reject_min` | 0.70 | the default bar |
| `media.max_bytes` | 15,000,000 | one attachment |
| `media.max_pixels` | 40,000,000 | decoded image, against decompression bombs |
| `media.ocr_text_reject_min` | 0.99 | text read off a picture |
| `media.allow_unchecked_video` | `false` in code, **`true`** in `settings.yaml` | video uploads |
| `media.allow_unreadable_image` | `false` | unreadable writing |
| `media.allow_unreadable_image_when_seen` | `true` | unreadable writing on a cleared picture |
| `ocr.max_pages` | 60 | per upload |
| `ocr.dpi` | 200 | page rendering |
| `ocr.min_confidence` | 0.5 | below this, not trusted |
| `visual.reject_min` | 0.90 | written-policy labels |
| `visual.probe_reject_min` | 0.70 | the trained probe |
| `sentence_scan.low_syntax_reject_min` | 0.90 | spans with no grammatical substance |
| `trust_proxy_hops` | 0 | `X-Forwarded-For` depth |

Environment overrides exist for several of these — `THRESHOLD_ALLOW_MAX`,
`THRESHOLD_REJECT_MIN`, `VISUAL_REJECT_MIN`, `VISUAL_PROBE_REJECT_MIN`, `VISUAL_CHECK=0`,
`IMAGE_OCR=0`, `DEMO_PAGE=1`, `API_DOCS=0`, `ADMIN_TOKEN`, `MODEL_DIR`. See
`app/config.py`.

## 12.4 Error codes

**Submission**

| Code | Status |
|---|---|
| `invalid_json`, `invalid_content`, `invalid_content_type`, `invalid_content_id` | 400 |
| `client_blocked` | 403 |
| `content_too_long` | 413 |
| `model_not_ready`, `queue_unavailable` | 503 |
| `inference_failed` | 500 |
| `not_supported` (status lookup in sync mode) | 404 |

**Media**

| Code | Status |
|---|---|
| `invalid_file` | 400 |
| `not_found` (file moderation disabled) | 404 |
| `media_too_large`, `content_too_long` | 413 |
| `media_unreadable`, `media_unsupported`, `media_not_checkable` (video) | 422 |
| `media_not_checkable` (OCR or visual model threw) | 503 |

**Admin**

| Code | Status |
|---|---|
| `invalid_request`, `weak_password`, `invalid_code`, `invalid_token`, `bad_format`, `bad_scope`, `bad_address`, `missing_value`, `bad_ids`, `bad_mode`, `missing_ids`, `missing_preset`, `missing_phrasing`, `invalid_updates` | 400 |
| `unauthorized`, `invalid_credentials` | 401 |
| `forbidden` (missing CSRF header) | 403 |
| `not_found`, `no_such_surface`, `no_such_label`, `not_blocked`, `file_missing`, `no_such_image` | 404 |
| `policy_conflict`, `would_block_self`, `category_exists`, `no_default`, `no_embeddings` | 409 |
| `rate_limited` | 429 |
| `save_failed`, `policy_build_failed`, `reload_failed`, `training_error`, `preset_error` | 500 |
| `export_unavailable` | 503 |

## 12.5 Known gaps

Recorded so they are decisions rather than surprises.

**`group_post` and `network_article` are outside the grievance check.** The two new surfaces
are public ones, so a buyer complaining about a product in a group post is the same traffic
the grievance check exists to protect — but the surface lists that control it still name only
`article`, `post` and `advertisement`:

- `DEFAULT_SURFACES` in `app/grievance.py`
- `surfaces: [article, post, advertisement]` on `complaint.public_grievance` and its
  companion rule in `config/gate_patterns.yaml`

Until both lists include the new names, a genuine complaint on either surface is scored by
the general model with no complaint cover, which is the case that produces a wrong reject.
Those files belong to the moderation-levels work, so the change is theirs to make, not a
side effect of adding the surfaces.

**Two error envelopes.** See section 1. The admin blueprints return a flat
`{error, message}` while `app/api.py` returns nested `{error: {code, message}}`, and
`check_admin()` mixes the two on a single endpoint. Every client must handle both.
Unifying them is a breaking change for anyone already parsing the flat shape, so it needs
a deliberate version bump rather than a quiet fix.

**The OpenAPI spec is hand-written, not generated.** [docs/openapi.yaml](openapi.yaml)
covers all 50 routes and is checked against the route decorators, but nothing enforces
that automatically — add a CI step that diffs the spec against the blueprints, or move to
generation (`flask-smorest`, `apispec`), before it drifts.

**The result contract is still a draft.**
[contracts/moderation_result.md](../contracts/moderation_result.md) is marked `DRAFT`
pending the Data team's sign-off, despite being the API↔Data hand-off document.

**`/v1/moderate` has no authentication and no rate limit.** It is the public, expensive
endpoint — every call runs ONNX inference, and the media variant also runs OCR and CLIP.
The only thing in front of it is the IP blocklist. Rate limiting exists in this codebase
(`Admin.limited`) but is wired only to the five auth endpoints. This is safe when the
service listens on a private interface reachable only from the platform backend; confirm
that is the deployment topology.

**`POST /v1/admin/model/reload` does not use `check_admin()`.** It is the one admin
endpoint that rejects a logged-in session. Defensible for a dangerous operation, but it
is an inconsistency worth knowing about.

**The two moderation-settings APIs collide.** See the warning in section 7.

**`features:` in `moderation_levels.yaml` ships as `{}`.** Every surface currently falls
through to one default policy, despite the file's own commented table arguing for
per-surface levels.

## 12.6 Integrating from an ASP.NET Core platform

The platform's content endpoints are controller-based
(`/api/Post/createPost`, `/api/GroupPost/CreateGroupPost`, `/api/Article/CreateArticle`,
`/api/NetworkArticle/CreateArticle`). Call `POST /v1/moderate` from the create and update
handlers, before persisting.

| Platform controller | `content_type` |
|---|---|
| `Post` | `post` |
| `GroupPost` | `group_post` |
| `Article` (company) | `article` |
| `NetworkArticle` (networking) | `network_article` |

All four map one-to-one, so each surface carries its own strictness setting
(`PUT /v1/admin/moderation-levels/<content_type>`) and each is distinguishable in the
audit log after the fact. `group_post` and `network_article` were added for exactly this;
before they existed, half of that traffic had to be sent as `post` or `article`.

Three things to settle:

1. **Group posts and network articles are outside the grievance check.** Both are public
   surfaces, so a buyer's product complaint on either is the traffic that check exists to
   protect — but its surface list still names only `article`, `post` and `advertisement`,
   and a surface it does not name is not checked. Until that list is extended (see 12.5), a
   genuine complaint on those two surfaces is scored by the general model alone, which is
   the case that produces a wrong reject. Sending group posts as `post` instead would buy
   the complaint cover at the price of the separate strictness setting and the audit
   distinction — a policy decision, not a default to fall into.

2. **Both article surfaces address the author as "article".** `network_article`'s noun in
   `config/feedback_messages.yaml` is `article`, so a networking author reads "Your article
   needs a few changes". The stored `content_type` still tells the two apart; change the
   noun in that one line if the wording should differ.

3. **Pass `content_id` on updates**, so revisions of the same item are linked in the
   audit trail. This matters most for your four `PUT` handlers: without it, an edit looks
   like a brand-new submission in the log.

Remember to send the **joined** form fields as one `content` string (section 2), and to
route attachments through `POST /v1/moderate/media` — otherwise the text is checked and
the pictures go out unexamined.
