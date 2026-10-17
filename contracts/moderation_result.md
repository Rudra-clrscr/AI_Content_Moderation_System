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

Every one of these is clickable at `/articles` with `DEMO_PAGE=1`: the tab bar is live, each
tab opens its own form, and the `content_type` it sends is printed above that form.

**Still pictures are now inspected; footage still is not.** *Changed in 1.7.* An uploaded
image gets two passes: OCR reads any writing in it, and the CLIP visual check
(`flask_api/app/clip.py`) scores the picture itself against the written policy in
`flask_api/config/visual_policy.yaml` — see "The visual check" below. **Video is still not
analysed at all**: no frame or audio decoding, so a video's title and description are moderated
while the footage is not.

The platform's current policy is to publish unwatched footage rather than close the tab:
`media.allow_unchecked_video` is **on**, and such a result says so in the record
(`checked: false`, `visual_content_checked: false`) instead of passing the file off as clean.
Turn the flag off the day footage must not go out unwatched. Video remains covered by human
reporting, not by this service.

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
| `image` | OCR **and** the visual check | Both run on the same decoded picture. Text is moderated — at a higher bar than typed text, see below — and the picture itself is scored against the visual policy, where a confident unsafe label **rejects** the upload (*1.7*). An image with writing OCR could not read is **allowed** when the visual check looked at the picture and cleared it, carrying `checked: false` for its words (*1.7*); it is **refused** (`422 media_unreadable`) when nothing looked at it either. With the visual check off or its model missing, this row behaves exactly as it did in 1.6 |
| `video` | Nothing | **Allowed unchecked** (`checked: false`) while `media.allow_unchecked_video` is on, as it is in the shipped config; **refused** when it is off |
| anything else | — | `422 media_unsupported` |

The kind comes from the file's leading bytes, never its name: a `.png` that is really a PDF is
treated as a PDF, and an executable renamed `.jpg` is refused.

Every response carries a `media` block:

```json
"media": {"filename": "flyer.png", "kind": "image", "text_found": true,
          "text_readable": true, "ocr_confidence": 0.9937,
          "visual_content_checked": true,
          "visual": {"label": "safe", "score": 0.9912, "unsafe_score": 0.0088,
                     "scores": {"gore": 0.0012, "safe": 0.9912, "sexual": 0.0034, "weapon": 0.0042}}}
```

`visual_content_checked` means what it says: `true` only when the picture itself was scored.
It is `false` wherever the check is off, its model is missing, or the file is a video — and a
client **must not** read that as clean. `visual` is present only when it is `true`, and carries
`category` only when the best-matching label is an unsafe one.

`text_found` is whether there is writing in the file at all; `text_readable` (*added in 1.6*)
is whether OCR actually read it. The three states are distinct, and a client must not collapse
them:

| `text_found` | `text_readable` | What it means | Result |
|---|---|---|---|
| `false` | `false` | No writing in the picture (a product photo) | `allow`, `visual_content_checked: false` |
| `true` | `true` | The writing was read and moderated | the usual decision |
| `true` | `false` | There **is** writing and OCR could not read it | `allow` with `checked: false` when the visual check cleared the picture; `422 media_unreadable` when it did not run (*changed in 1.7*) |

A file with nothing to read (a photo with no text, or an unchecked video) still returns a
normal `allow` result and is still written to the audit log, so the record shows both what was
published and that nothing in it could be read.

**Why an image with no text is allowed but a blank PDF page is not:** a product photo with no
writing on it is completely ordinary, and refusing those would break the feature for every
honest seller. A PDF page with no text is not ordinary — it is a scan, and scans are how a
screenshot of a scam arrives. The asymmetry is deliberate.

**Why writing OCR can't read is no longer refused outright** (*changed in 1.7*). It used to be,
and the reason was sound: nothing had inspected the picture, so an unread caption was the whole
of what was known about it. The visual check changed that half of the problem — the picture is
now scored — while the other half, the words, stays unknown. Measured on 63 real BONC listing
pictures, the blanket refusal was turning away **19%** of them over logos, number plates and
watermarks: `'D'`, `'CRUK'`, `'00 0000'`, `'星'`. All 63 now publish.

The case the old rule actually guarded against is covered at the source instead: the Devanagari
recognition model is bundled, so Hindi is **read** rather than falling into this path at all.
Verified end to end — a rendered Hindi threat is rejected by `abuse.hi_targeted`, benign Hindi
signage publishes. Set `media.allow_unreadable_image_when_seen: false` to restore the old
refusal.

**The original reasoning, for the record.** Detection finds where the words
are; recognition turns them into characters. RapidOCR bundles a Latin and a Chinese recognition
model, so a Hindi picture came back with every line located and near-nothing recognised — and
that used to be reported as an ordinary `allow` with a clean record. A Devanagari threat
published that way (QA, 2026-09-30). Recognition confidence alone does not catch it (0.62,
above the 0.5 floor); the signal is how many characters came back per detected line, which is
25–28 for English read properly and 1.5–4 for Hindi read by the wrong model. See
`ocr.min_chars_per_box` and `ocr.trust_short_confidence` in `settings.yaml`.
Set `media.allow_unreadable_image: true` to publish these anyway — they then carry
`checked: false`, never a clean result. To *read* them instead, bundle the matching
recognition model (`python scripts/fetch_ocr_langs.py devanagari`, then `ocr.rec_model_path`
and `ocr.rec_lang`); Hindi pictures are then extracted and judged like any other text.

Media errors: `413 media_too_large`, `422 media_unsupported` / `media_not_checkable` /
`media_unreadable`, plus all the PDF codes below. `503 media_not_checkable` is different from
the 422 of the same name: OCR itself failed, so nothing is known about the file either way —
the upload is still refused, but the caller should offer a retry rather than tell the author
their file is the problem.

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
  "schema_version": "1.9",
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
| `decided_by` | `gate` means a Layer 1 rule decided: a `block` rule (model skipped), or a `revise` rule on content the model would have allowed. After a block, `risk_score`, `predicted_label`, `label_scores`, `model_version` and `latency_ms.inference` are then `null`. `model` means the model's score decided it. `targeted` means the whole text passed, but a sentence aimed at someone scored in the revise or reject band (see `targeted_segments`). `sentence` (added in 1.3) means one sentence, scored on its own, reached reject level (see `sentence_scores`). `disabled` (*added in 1.8*) means NOTHING was checked: moderation is switched off for this `content_type`, so the `allow` is not a judgement and `risk_score` is `null` — see the `moderation` block. `grievance` (*added in 1.9*) means a second model, consulted on PUBLIC surfaces only, refused the text as a customer-support complaint or an emotional outcry; `risk_score` is that model's score, and the main model is not consulted. Do not read it as a `model` decision: the main model scores the same text as safe on purpose, because in a proposal it is a member chasing a consignment. |
| `gate_matches` | A list of `{rule_id, category, action, spans}`. `spans` are `[start, end]` character offsets into `content` (added in 1.2). `block` rejects, `revise` sends the content back to its author, and `flag` is recorded only. |
| `sentence_scores` | *Added in 1.3.* For posts with 2 or more sentences: every sentence scored on its own, up to `sentence_scan.max_sentences`, as `{start, end, risk_score}` (offsets into `content`). *1.5:* an entry with `"by": "prefilter"` was scored by the linear triage filter instead of the model (`app/triage.py`) — its `risk_score` comes from that filter, is far below `allow_max`, and never contributes to the decision. Entries without `by` are model scores, as before. Any sentence in the reject band (v4: risk > 0.5) rejects the post (`decided_by: "sentence"`). *1.4:* sentences longer than `word_scan.window_words` words are also scored in overlapping word windows, listed here with `"kind": "window"` (so a long run-on sentence can't dilute a harmful phrase); these can appear even for a single-sentence post. Empty for short single-sentence posts, gate blocks, posts already rejected, or when the scan is off. |
| `word_scores` | *Added in 1.4.* The word-by-word scan, for each span the model flagged (risk above `allow_max`): every word as `{start, end, contribution}`, where `contribution` = risk of the span minus risk of the span with that word removed. The *trigger words* (in `feedback.issues[].words`) are the words with `contribution >= word_scan.min_contribution` (default 0.10). When no single word reaches that (the harm is spread over several words, or the score is saturated at 0.999), they are the smallest set, found greedily and at most `max_triggers`, whose removal brings the span back to ≤ `allow_max`. Long spans are analysed in their riskiest word window. Empty when nothing was flagged. |
| `feedback` | *Added in 1.2.* Author-facing guidance, and `null` when allowed. `{title, message, severity, issues, categories?}`. *1.7:* `severity` is the **moderation rating** shown to the author — see "The moderation rating" below. Each issue is `{start, end, source, message, rule_id?, category?, risk_score?, words?}`, where `start`/`end` are offsets into `content` for the client to highlight, `source` is `rule` or `model`, and `message` is the instruction to show. *1.4:* `words` lists the trigger words inside a model issue as `[start, end]` offsets; show them emphasised inside the highlighted sentence. For `reject`, `categories` names the policy areas. Since 1.4 the shipped config sets `highlight_on_reject: true`: with v4 there is no revise band, so a rejected author rewrites and needs to see what to change. Set it to `false` to show only the policy area. Wording lives in `flask_api/config/feedback_messages.yaml`. |
| `targeted_segments` | *Added in 1.1.* Sentences that mention someone (he, she, they, their…), scored separately: a list of `{start, end, risk_score, predicted_label}`, where `start`/`end` are character offsets into `content`. The text itself isn't repeated, so logs stay free of content. Empty when the check didn't run: gate block, whole text already rejected, no such sentence, or the check disabled. Not stored by the current SQL table; the Data team can add an `NVARCHAR(MAX)` JSON column if they want it. |
| `thresholds`, `model_version`, `gate_version` | These are included so every logged decision can be reproduced and audited after thresholds or models change. |

### The moderation rating (*added in 1.7*)

A member who is told only "this can't be published" has no sense of how far off they were.
`feedback.severity` is a **moderation rating** — `low`, `medium` or `high` — so the author
knows whether they are a word away or nowhere near.

Words, not the probability: a member reads "high" and understands it, while a number only
invites nudging the text until it drops under the line. For the same reason the exact
`risk_score` stays out of what the author is shown, though it remains in the result for audit.

**The rating is a label, not a licence.** It does not change the decision and it is not a way
round it: content the moderator objects to is not published, whatever it is rated. There is no
"publish anyway" in this API, and a client must not build one.

**What the rating is read off.** Whatever actually decided, which is not always the whole-text
score:

| Decided by | Rating |
|---|---|
| the model (whole text, a targeted segment, or one sentence from the scan) | the **highest** risk of the whole text and of the spans in `issues` — a paragraph scoring 0.1 that holds one sentence at 0.99 is rated `high`, not `low` |
| a gate `block` rule | `high`. Nothing was scored: the rule decided on its own, and `risk_score` is `null` |
| a gate `revise` rule on text the model was happy with | `medium`. The model's own score would read as `low` and understate a deterministic policy hit |

Bands: `high` at risk ≥ 0.80, `medium` at ≥ 0.50, `low` below that (`app/feedback.py`
`SEVERITY_BANDS`). They are deliberately coarser than the 0.5 decision boundary, so `medium`
covers a borderline reject and `high` means the model was not in two minds.

Nothing else about the result changes, and a caller that ignores `severity` behaves exactly as
it did under 1.6.

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

## Text read out of a picture is judged at a higher bar (*added in 1.7*)

Words a member types are prose. Words OCR pulls off a shopfront are not: `'TURNKEY MWRULTANTS A
SOLUTIONS'`, `'RADIATORS RADIATORS &ACCESSORIES ACCESSORIES HEAT YOUR HOME IN STYIL ACSES'`. The
moderation model was trained on sentences and scores that noise unreliably — it put four ordinary
BONC listings in the reject band at 0.78–0.976.

So an image's text is routed at `media.ocr_text_reject_min` (0.99) instead of the ordinary
`thresholds`, and at **one boundary rather than two**: a `revise` verdict means "edit the
highlighted part", which cannot be done to words baked into a photograph — the author can only
replace the picture. The `thresholds` block in the result reports the boundaries actually
applied, so a decision stays reproducible.

Measured with `scripts/eval_ocr_text_bar.py`, scoring rendered text read back through the real
OCR stack:

| set | n | min | p50 | max |
|---|---|---|---|---|
| scam flyers | 10 | 0.9996 | 0.9997 | 0.9997 |
| threats | 8 | 0.0738 | 0.9996 | 0.9997 |
| business signage | 10 | 0.0002 | 0.0002 | 0.0009 |
| real BONC listings | 43 | 0.0002 | 0.0002 | 0.9281 |

Any bar in **[0.95, 0.999]** catches 17 of the 18 harmful images and refuses none of the 53
honest ones. The 18th — *"Stop trading or face consequences, we have your address"* at 0.074 —
is missed at **every** bar including the ordinary 0.5, so it is a gap in the model rather than
in this threshold.

**This moves the model's boundary only.** Gate rules are unaffected: a rule either matched or it
did not, and a scam phrase photographed is still a scam phrase. PDFs keep the ordinary bar —
their text is extracted, not guessed at, and reads as what the author wrote.

## The visual check (*added in 1.7*)

Everything else in this service reads text. That left a hole this contract was always explicit
about: an image was OCR'd for words and the *picture* was never judged, so a photograph of
anything at all published as long as it carried no harmful words. `app/clip.py` closes it for
still images.

**How it decides.** CLIP (Contrastive Language-Image Pre-training) embeds pictures and
sentences into one space, so a picture can be scored against a written description rather than
against trained classes. The policy is therefore a config file — a handful of phrasings per
label in `flask_api/config/visual_policy.yaml` — and adding a category needs no retraining, no
labelled pictures and no code change. Scores are a softmax across every phrasing, which is why
the file carries a long list of *safe* phrasings too: they are what the unsafe labels are judged
against, and a thin safe list makes ordinary product photos look suspicious.

An unsafe label at or above `media.visual.reject_min` **rejects the upload**, as a `decision:
"reject"` with `decided_by: "gate"` and a synthetic gate match (`rule_id: "visual.weapon"`,
`category: "weapons"`). It is deliberately shaped like a blocked phrase: same feedback wording,
same audit row, same sink, so a caller that already handles a text rejection handles this one
with no new code. The `content` is the filename, because there is no text to quote — what was
wrong is in `media.visual`.

**It is a separate model from the text moderator.** `models/img_v1`
(`clip-vit-b32-bonc-img-v1`) scores pictures; `models/v6` (`deberta-v3-small-bonc-v6`) scores
words. They version apart, roll back independently, and neither stands in for the other. Only
the 352 MB vision encoder is loaded at serving time: the text encoder runs once offline in
`scripts/build_clip_prompts.py`, which bakes the prompt embeddings into `prompts.npz`.

**What it is measured at.** 1,168 sample images, `scripts/eval_clip_images.py`:

| `reject_min` | weapons | gore | objects | BONC listings | human bodies |
|---|---|---|---|---|---|
| 0.50 | 93% | 98% | 5/200 | 0/63 | 17/105 |
| 0.80 | 83% | 95% | 2/200 | 0/63 | 7/105 |
| **0.90** (shipped) | **77%** | **93%** | **0/200** | **0/63** | **3/105** |
| 0.95 | 72% | 86% | 0/200 | 0/63 | 1/105 |
| 0.97 | 67% | 79% | 0/200 | 0/63 | 0/105 |
| 0.99 | 57% | 64% | 0/200 | 0/63 | 0/105 |

The last three columns are false positives. **Human bodies** (`images/body_parts`: 105 pictures
of athletes, physiotherapy, anatomy diagrams and swimming, collected from Wikimedia Commons
with a licence and a source URL for every row) was added to measure the `sexual` label, which
has no positive samples behind it — and it immediately found that **27% of that set scored
unsafe** at the shipped threshold, `gore` firing on skin and medical imagery as much as
`sexual` on athletes. There was nothing in the safe list for a body to *be*, so the nearest
match was an unsafe label. Fixing that cost 6 points of weapon recall and 2 of gore.

**Re-run the script after any change to `visual_policy.yaml`.** The numbers move with the
prompts, and the lesson has now cost recall three times: **name the scene, not the pose.** A
prompt describing a body position ("a person exercising or training") also describes a person
aiming a gun and outscored 30 weapon images on its own; "a person lifting weights in a gym"
does not.

**A trained probe decides alongside the prompts** (*added after the first measurement round*).
`models/img_v1/probe.npz` is a **five-class** logistic regression — `safe`, `weapon`, `gore`,
`minor`, `revealing` — over the frozen encoder's embeddings, fitted by
`training/train_image_probe.py` — the same shape as Layer
1.5's triage filter, and served the same way, as one matrix multiply. A picture is refused if
the prompts cross `visual.reject_min` **or** the probe crosses `visual.probe_reject_min`; they
make different mistakes, so together they cover more than either alone.

Measured out-of-fold with **grouped** cross-validation, so near-duplicate video frames never
straddle a fold:

| | weapons | gore | unrest | BONC listings refused |
|---|---|---|---|---|
| prompts only (0.90) | 77% | 93% | 49% | 0/63 |
| + probe (0.90) | 85% | 97% | 49% | 0/63 |
| **+ probe (0.70, shipped)** | **93%** | **99%** | **49%** | **0/63** |

`unrest` (*category* `violence_or_unrest`) is carried by its prompts alone. It exists because
violence and anger should not be promoted on a networking platform — **not** to keep politics
out: a post about a tariff, or about a transport strike delaying deliveries, is ordinary trade
talk and is not what the prompts describe. They describe confrontation: riots, baton charges,
burning effigies, stone-throwing. 4 of 90 ordinary crowds (trade fairs, queues, wedding and
temple processions) are refused by it, and no real BONC listing is.

Its 49% is the weakest number in this table and is honest: a seated sit-in, a candlelight vigil
and a baton charge look nothing alike, and ten English phrasings cover that range poorly. It
was tried as a fourth probe class and **dropped** — on 90 examples it cost 13 of 90 safe crowds
and 10 of 105 body parts while gaining nothing on weapons or gore, because the difference
between a protest crowd and a trade-fair crowd is posture and expression rather than the scene.
More images of both, then re-fit, is the route to improving it.

`wedding` (*category* `wedding_content`) refuses wedding photography outright, on the same
reasoning as `romantic`: a networking platform is not where a member posts their wedding album.

It is the easiest label in this file by a distance. A mandap, a garland exchange, a sacred fire
and a red lehenga are a visual vocabulary with no business equivalent, so nine prompts separate
it perfectly — **205 of 205** on the supplied set, whose score distribution starts at **p25
0.909** while the whole of `crowds_safe` tops out at 0.403. Nothing sits between 0.41 and 0.90
except a single nikah photograph at 0.55, so the bar is placed in that void: **0.50**, with
0.45 through 0.51 giving identical numbers on every other set.

That perfection took two rounds, and the second is the more interesting one. A **Sikh** wedding
has no mandap, no sacred fire, no garland exchange and no white dress, so the first nine
prompts described a Hindu or Western ceremony and nothing else. A supplied Anand Karaj scored
**0.115**, and the prompt beating it was `"a photo of a religious procession or temple
festival"` from the safe list — which is fair, because a gurdwara ceremony *is* a religious
gathering. Two prompts naming the scene (the couple before the Guru Granth Sahib, a groom in a
turban) fixed it at no cost at all. Naming the *venue* instead — "a sikh wedding ceremony in a
gurdwara", "a punjabi wedding couple" — was tried and dropped: both refused an ordinary crowd,
because the festivals held in those buildings look like the weddings held in them.

**The risk was never the weddings — it was the event trades.** A caterer, a decorator, a
florist, a banquet hall and a lighting hire firm are ordinary BONC members who sell *into*
weddings, and nothing in the safe list described any of them. Six prompts now do (banquet hall,
buffet, flower arrangement, stage lighting, crockery, marquee), and they are why the label costs
nothing measurable: `BONC_Positive` stays **0/62**, `crowds_safe` **8/206**, `allow` **5/200**,
`body_parts` **19/105**. `romance` went *up*, 57% to 62%.

*(`BONC_Positive` is 62 distinct pictures, not the 63 files it holds — two are byte-identical.
It was quoted as 0/63 for most of this branch's life. `eval_clip_images.py` now de-duplicates
every set by content hash, which is also what stopped `minors` from reporting 282 images once
the walk went recursive and found `minors/kid_neg/`: 73 of its 74 files are byte-identical to
pictures already in `minors/`, and counting them twice would have let a tenth of that set vote
twice in its own recall figure.)*

Two things had to be unpicked to make the rule coherent:

- *"a photo of a wedding procession"* was in the **safe** list, added as an `unrest`
  counterweight. It said the opposite of the new rule and was removed; `crowds_safe` is
  unchanged without it, because the trade fairs, queues, temple festivals and parades beside it
  carry that job on their own.
- **14 of the 220 `crowds_safe` images were wedding processions**, collected as `allow`. Under
  the rule they are positives, so leaving them there would have scored the policy as wrong
  every time it was right. They moved to `images/weddings_commons` with their licence, source
  URL, attribution and SHA256 intact.

That split set catches only **2 of 14**, and the reason is worth stating rather than fixing:
looking at them, most are not legibly weddings at all — two are historical miniature paintings
of processions, one is a decorated car on a road, several are night street scenes. The one
unambiguous wedding photograph in it (a bride's portrait) scores 0.855. It is the same pattern
as the collected `violence` set: Commons is diverse without being representative, and chasing
that 14% would mean learning to refuse street crowds and museum artwork.

`physique` (*category* `physique_display`) draws two lines at once:

1. **A member showing off their own body is not a business posting**; a gym, an equipment
   supplier or a supplement seller promoting itself is.
2. **Promoting a business does not launder the picture.** A gym flyer, a fitness poster and a
   supplement banner are all legitimate B2B postings, and they are still refused when they are
   built around a bare chest. How much body the advert puts on the page does not stop mattering
   because a company paid for the layout.

Measured on 72 gym physique photographs against 49 gym business pictures, both supplied:
**99% caught (71/72), 5 of 49 refused.** Those five do not divide the way a false-positive
count suggests, so they are worth reading individually:

| | |
|---|---|
| 1 | pre-existing `manhandling` false positive on a cable-machine banner — not this label |
| 3 | gym posters built around a **shirtless** man. Rule 2 says refuse them; they are in the allowed folder, and by the policy they are in the wrong one |
| 1 | borderline — a sleeveless top rather than a bare chest, physique-forward pose |

Rule 1 is a question about who a picture is addressed to, not what is in it: the blocked set
contains fully clothed mirror selfies and the allowed set contains a muscular model in a vest
demonstrating a chest press. Neither skin nor the room separates them. That is the same framing
question `minor` asks and it is answered the same way — **in the prompts, not the probe.** A
linear model over one global embedding cannot ask who a picture is for.

The single miss is a shirtless man in front of a gym mirror, which *"a photo of people
training together in a gym"* wins because the reflection makes two of him.

**One safe prompt was the whole problem, twice over.** A reported upload — a shirtless man on a
lat pulldown — published cleanly, and the reason was not the AVIF path (`detect_kind` reads it
as an image and the picture was scored) but *"a product photo of a gym machine with a person
demonstrating it"*. At 0.284 cosine it was the best match in the file, above every `physique`
prompt, because it described the picture exactly and said nothing about what he was wearing.
A 0.017 lead in cosine is 0.84 against 0.15 once the softmax has multiplied it by 100, which
is the thing to remember about tuning this file: the margins that decide a verdict are in the
third decimal place of a similarity.

It is now two prompts, *"a gym machine in a showroom"* and *"a person in a gym t-shirt on an
exercise machine"* — the same statement about the equipment, plus one about the clothes. That
took the label from 92% to 99% and moved nothing else: `allow` 5/200, `BONC_Positive` 0/63,
`body_parts` 19/105, `crowds_safe` 8/220, weapons 83%, minors 96%, nudity 95%, all identical.

The bar is **0.55**. 0.55 and 0.60 give identical numbers on every set here, so the lower one
is taken for the margin: that reported upload lands at 0.614, and 0.014 is not a gap to build
on.

One safe prompt changed to make this work: *"a photo of a person lifting weights in a gym"* is
now plural, *"people training together in a gym"*. In the singular it was the best match for a
solo physique shot and held the label to 83%; the body-part false positives it was added for
are not affected (that 105-image set went from 20 refused to 19 on the swap).

Six safe prompts were also added for trades the list had nothing for — construction,
laboratories, farming, hospitality, electrical, presentations. The safe list is what the unsafe
labels are judged against, and `test_there_are_safe_labels_to_be_judged_against` had started
failing: 65 safe prompts against 66 unsafe ones. A picture matching no safe prompt has nothing
to be safe *as*. It cost one `unrest` image, 129 to 128, and nothing else moved.

The grouping matters more than it sounds: with a random split the weapons figure reads 95% and
the honest one is 73%. 300 frames from a handful of videos are easy to memorise and hard to
generalise from, and the probe alone does **not** beat the prompts on weapons (73% against
77%) — it is the combination that wins. `media.visual.probe_reject_min` in `settings.yaml`
carries the full sweep; deleting `probe.npz` falls back to prompts alone.

`app/clip.py` also serves SigLIP 2 bundles (`scoring: sigmoid`, independent per-label scores)
as well as CLIP's softmax. SigLIP was measured and **not** adopted: with its own sigmoid
calibration it reaches 57% weapons / 68% gore at matched false positives, and with softmax it
is roughly level with CLIP for about four times the compute (196 vision tokens against 49).

**What it does not do.** The prompts are zero-shot, so a label with no probe class behind it
is only as good as its wordings, and most of these labels are measured on sets collected for
the purpose rather than on BONC's traffic.

`violence` and `threats` were the clearest example and are now a different kind of example.
They reported 47% and 38%, the two weakest figures here, and were quoted as the model's
weakness. Every picture in both was then looked at. **`threats` is about twenty photographs of
metal bands on stage**, plus manuscripts, candlelit vigils, Victorian engravings, a wild boar
and a crown; perhaps ten of its 86 are threatening. **`violence` is mostly boxing, wrestling
and MMA** — sport — with an FBI award handshake, Christmas markets, military swimming drills
and a meerkat among the rest.

Neither set can measure recall, so both are registered as mixed-expectation and excluded from
the aggregates, like `non_graphic`. Read the other way, the model *refused* 67 and 31 of them,
so a good share of those refusals were false positives hiding inside a recall figure: the
finding is not that the model was weak there, but that nobody knew which way the error ran.
`unrest_real` — 84 supplied press photographs of the same category — measures **99%**, and is
what to trust when they disagree. `docs/image_sets.md` records the audit.

There is still no counterfeit detector, and video is still not looked at.

**Failure behaviour differs by cause, on purpose.** Missing model or `enabled: false` →
the check is simply off, results say `visual_content_checked: false`, and the service behaves
exactly as it did in 1.6. It is never a hard dependency. But a model that is present and
*throws* → `503 media_not_checkable`, refusing the upload: publishing a picture nothing looked
at is the hole this exists to close.

**Where it runs.** `media.visual.device` is `auto` | `cuda` | `directml` | `cpu`. `auto` takes
the best execution provider onnxruntime actually offers, and a GPU that is named but not
installed falls back to CPU with a warning rather than refusing to start. Measured on an RTX
4050 laptop: 14.8 images/s on 6 CPU cores against 60.5 on CUDA, a 4.1x speed-up with **identical
decisions** — 273 images scored both ways gave the same label and the same verdict for every
one, with scores differing by at most 8.5e-03. The GPU build of onnxruntime replaces the CPU
one rather than sitting beside it; `flask_api/requirements.txt` has the two commands.
`GET /ready` reports the provider actually in use, so whether pictures are being looked at is
visible without reading a published result.

## The public-surface grievance model (*added in 1.9*)

A customer-support complaint published as an article is not the same act as the same sentence
sent to a counterparty in a proposal. The first is a complaint about a company, hosted on a
trade platform that is not that company's channel; the second is the platform working.

The main model cannot draw that line, because it is given text and no surface, and it must not
be taught to: an earlier round that refused this language made the portal useless for disputes,
and `res/public_callouts.csv` still carries ~19,600 rows labelled *allow* to hold that fix in
place. So a second model answers the other question —

> would this be a customer-support complaint or an emotional outcry if it were PUBLISHED?

— and `settings.yaml` names the surfaces it is consulted on (`article`, `post`,
`advertisement`). On any other `content_type` it never runs, including a new one: a classifier
that refuses a whole genre on a surface nobody reviewed is the regression this design exists to
prevent.

| | main model | grievance model |
|---|---|---|
| question | is this harmful anywhere | is this a complaint published where it does not belong |
| surfaces | all | `article`, `post`, `advertisement` |
| `decided_by` | `model` | `grievance` |
| "nobody has replied about my refund in two weeks" | allow | refuse on an article, never asked on a proposal |

`GRIEVANCE_CHECK=0` turns it off; the gate's `complaint.public_grievance` rule is then the only
cover, as it was before this existed.

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

<hashtags as the words they spell>][

<link URL 1>
<link URL 2>...]
```

**Hashtags go as the words they spell, not as the tags.** `#FirstCopyRolex #ReplicaWatches`
is sent as `First Copy Rolex Replica Watches`. A tag runs its words together and the model
reads sentences, so the joined form is close to invisible to it - measured on v5 with a clean
article plus three tags, the counterfeit set scores **0.000 as typed and 1.000 split**, and the
adult set 0.001 against 0.999. Keeping the `#` on is worse than dropping it. Only camelCase,
digit and underscore boundaries can be recovered, so a tag typed all in lower case
(`#firstcopyrolex`) stays one word and remains weak; the title and body are still the main
defence. `normalizeHashtag` turns separators into camelCase humps rather than deleting them, so
those boundaries survive to be split again.

Because the moderated text is not the text in the input box, a hashtag issue is reported
against the field rather than highlighted character by character - the same treatment a link
issue gets.

Link URLs must be included, because a scam domain hidden behind "click here"
isn't part of the visible text. `flask_api/app/static/articles/moderation-client.js`
does all of this (`buildArticleContent`, `mapIssues`) and can be copied as-is.

**A model issue's span never crosses a line break**, so every issue belongs to exactly one
field and `mapIssues` can attribute it without guessing. The splitter always ended a sentence
at a newline, but the step after it merged a fragment of under three words into the next
sentence — so a short title was absorbed into the first body sentence and came back as one
issue covering both, highlighting a clean business name as the problem (QA, 2026-09-30, on
`"Bhavani Textiles\n\nBuy from us or we will burn your shop down…"`, which returned a single
issue spanning 0–84). Merging now stays within a line. Gate issues come from regex matches
over the whole text and are not bound by this, so keep handling an issue that spans two
fields; it just no longer happens for model issues.

## Showing feedback to the author (platform front end)

When `decision` is not `allow`, mark the author's own inputs first: render `content` with each
`feedback.issues[i]` range (`start` to `end`) highlighted, with the issue's `words` emphasised
inside it, and show each issue's `message` next to the input it belongs to.

Then show the **"before you post" popup** over it:

* `feedback.severity` as the moderation rating, in words — not the probability.
* `feedback.title` and `feedback.message`, and the issues quoted.
* One way forward: *change the content*. Dismissing the popup — the button, the close icon,
  Escape, a click outside it — all do the same thing, because there is only one thing to do
  next. **Do not add a "publish anyway" button**: the popup tells the author what is wrong, it
  does not negotiate over whether it gets published.

Keep the author's text editable throughout, and resubmit it to `/v1/moderate` as a new request.

Working references, all framework-free:
`flask_api/app/static/articles/moderation-client.js` (`mapIssues` for the highlighting,
`showDecision` for the popup — it builds its own DOM, so a surface needs no markup of its own),
`articles.js` (the Articles editor) and `surfaces.js` (every other tab). The demo page
(`/demo`, `flask_api/app/static/demo.html`, function `nudge`) is the minimal version.

## Open questions for the Data team

1. In sync mode the insert still happens inside the request. Should it move to a background writer or queue so API latency doesn't include the DB round-trip?
2. ~~Should raw `content` be stored?~~ Settled: hash only.
3. ~~Should `request_id` be the primary key?~~ Settled: yes.
4. Queue and broker names: I've assumed queue `moderation` and Redis DBs 0 (broker) and 1 (results). Are those right?
5. Do the four `content_type` values match your schema's enum?
