# Swagger / API test report

**Run date:** 2026-11-01 · **Branch:** `feature/api-contract-docs-and-two-new-surfaces`
**Result:** 120 live HTTP checks, 118 passed · 895 unit tests, 895 passed · 0 spec-to-code drift

This is the acceptance run for [openapi.yaml](openapi.yaml) and [API_REFERENCE.md](API_REFERENCE.md):
the two documents say what the service does, and this is the record of checking that claim
against a running instance rather than against the source.

**What ships from this run: text and image moderation. PDF does not.** The 29 checks in
[section 8](#8-part-6--the-pdf-path-2729) were run after the first 91, found two defects in the
scanned-PDF path, and that path was taken out rather than deployed — `app/pdf.py` and its tests
are gone from the repo, and a PDF is now published unread with `checked: false`. Section 8 is
the evidence behind that call; [section 3.2 of the reference](API_REFERENCE.md) is the contract
that replaced it.

Six defects were found in all, and the counts in this report are the runs as they happened:
the first 91 checks all passed; of the 29 PDF checks, 27 passed, one failure was a wrong
assertion in the test, and one was a real defect.

Two findings are **not** fixed here, and they are the two worth reading:
[section 7](#7-what-this-run-did-not-settle) — the visual check refuses roughly one in five
pictures its own teaching data marks allowed — and D6 in
[section 6](#6-defects-found), the exemplar behind it, which is also what stopped PDF.

---

## 1. What was under test

| | |
|---|---|
Base URL | `http://127.0.0.1:8000` (dev server, `python wsgi.py`) |
Mode | `sync`, `model_backend: onnx` |
Text model | `bonc-v9-ver3`, schema `1.9` |
Visual | CLIP, `CUDAExecutionProvider`, 173 prompts over 16 labels, softmax, probe on at `0.7` |
Gate | 19 rules, `gate_version: 2` |
Python / Flask | 3.12.10 / 3.1.3 |
Spec | OpenAPI 3.0.3, 42 paths, **49 operations**, 8 tags (43 and 50 before PDF came out) |
Swagger UI | 5.17.14, pinned, served from this app's own origin |

Everything was driven over real HTTP with `requests`. Nothing used the Flask test client, because
the test client does not exercise the WSGI layer, the header handling or the multipart parsing —
which is where two of the four defects were.

**Scope:** the moderation endpoints and the admin endpoints. Those are what gets deployed to the
AI/ML server, so those are what this report covers. The demo pages are not in scope.

---

## 2. Results

| Part | Area | Checks | Result |
|---|---|---|---|
1 | Moderation API — surfaces, payload, validation | 35 | **35 pass** |
2 | Media — images, PDFs, refusals | 13 | **13 pass** |
3 | Admin — auth, levels, audit log, blocked clients | 31 | **31 pass** |
4 | Image dataset + visual policy reads | 12 | **12 pass** |
6 | **PDF path** — scans, offsets, limits, damage | 29 | **27 pass** ([section 8](#8-part-6--the-pdf-path-2729)) |
| | **Total** | **120** | **118 pass** |

There is no part 5: the scripts are numbered `t1`–`t6`, and `t5` was a re-check that re-ran two
assertions this report records as wrong and drew the 20-image sample in
[section 7](#7-what-this-run-did-not-settle), rather than a part of its own.

Separately: `pytest flask_api/tests` → **895 passed** in 3m 21s, and a static check that parses the
blueprint decorators and diffs them against the spec → **49 operations in the code, 49 in the
spec, no drift**, with the `ContentType` enum at 11 values in both documents in the same order.

The unit count fell from 932 to 895 and the route count from 50 to 49 in the same change: PDF
extraction was removed after part 6, taking `tests/test_pdf.py`, five scanned-PDF tests in
`test_visual_check.py`, three in `test_media.py` and the `POST /v1/moderate/pdf` route with it.
Four tests were added for the behaviour that replaced them.

---

## 3. Part 1 — the moderation API (35/35)

### All eleven surfaces are accepted and scored

Each surface was sent the same benign sentence ("Our mill ships cotton bedsheets nationwide").
All eleven returned `200`, `decision: allow`, `risk_score: 0.000188` — identical, which is the
correct result: **the surface does not change the score.** It selects the wording the author sees
and is stored with the decision.

`business_profile` · `product_listing` · `post` · `group_post` · `advertisement` · `article` ·
`network_article` · `video` · `request` · `proposal` · `business_proposal`

### The surface changes the wording, not the verdict

The same abusive sentence, five surfaces, each refused by the model with its own noun:

| `content_type` | `feedback.title` |
|---|---|
`post` | Your post can't be published |
`group_post` | Your **group post** can't be published |
`article` | Your article can't be published |
`network_article` | Your **article** can't be published |
`proposal` | Your proposal can't be published |

`network_article` deliberately says "article". The member who wrote it does not think of the
distribution as part of what they wrote.

### The result payload

`schema_version: 1.9` · `content_id` echoed as a **string** (`4471` in → `"4471"` out) ·
`request_id` a UUID · `content_sha256` 64 hex · `model_version: bonc-v9-ver3` ·
`latency_ms.stages` present: `{grievance: 0.004, highlight: 0.026, whole: 30.268, words: 390.84}`

Word offsets were checked properly rather than just for presence: every `[start, end]` pair in
`feedback.issues[].words` lands inside the submitted text, and slicing the text at those offsets
returns real words — `you / worthless / idiot / and / quit`. An off-by-one here would highlight
the wrong part of an author's writing, and no assertion on the shape of the array would catch it.

### Validation

| Case | Expected | Got |
|---|---|---|
missing `content` | `400` | `400 invalid_content` |
`content` of only whitespace | `400` | `400 invalid_content` |
missing `content_type` | `400` | `400 invalid_content_type` |
unknown `content_type` (`comment`) | `400` | `400 invalid_content_type` |
`grouppost` — the plausible wrong spelling | `400` | `400 invalid_content_type` |
25,001 characters | `413` | `413 content_too_long` |
malformed JSON body | `400`/`415` | `400` |
form body on the JSON endpoint | `400`/`415` | `400` |
status lookup in sync mode | `404 not_supported` | `404 not_supported` |

The `400` for an unknown surface carries an `allowed` array, and it lists **all eleven** surfaces
in enum order. That array is what an integrator reads when they get the error, so it being
complete matters as much as the status code.

---

## 4. Part 2 — media (13/13)

> Three rows below describe the PDF extraction path, which was **removed** after part 6 found
> what [section 8](#8-part-6--the-pdf-path-2729) records. They passed when they were run and are
> left as they were; `/v1/moderate/pdf` no longer exists and a PDF is no longer read.

| Check | Result |
|---|---|
PNG with legible writing — OCR pass | `200`, `ocr_confidence: 0.9913`, `text_found: true`, `text_readable: true` |
PNG with no writing | `200`, handled cleanly, not a `500` |
Clean PDF via `/v1/moderate/media` | `200 allow`, `0.000169` |
Clean PDF via `/v1/moderate/pdf` | `200 allow`, `0.000169` — same verdict on both paths |
Abusive PDF | `200 reject`, `decided_by: model` |
Text file named `.png` | `422 media_unsupported`, `kind: unknown` |
No file at all | `400 invalid_file` |
Bad `content_type` on the media path | `400` |
Empty file | `422`, not a `500` |
16 MB attachment | `413` |

The `.png` case is the one worth keeping: the file was plain text with an image extension, and the
service classified it **from the bytes**, not the name. A service that trusted the extension here
would hand arbitrary data to an image decoder.

PDF fixtures were written by hand — `reportlab` and `fpdf` are not installed in this environment —
so the PDF path was exercised against a minimal but genuinely valid PDF 1.4 document with
extractable text, not against a library's output. Three checks over two one-page documents is
thin coverage for what that path claimed to do, which is why part 6 exists.

**Both synthetic images were refused** — `violence_or_unrest` via `visual.exemplar#3672`. Neither
contains anything of the sort; one is a green ellipse and the other is three lines of text about
bedsheets. See [section 7](#7-what-this-run-did-not-settle).

---

## 5. Part 3 — the admin API (31/31)

### Authentication, including the parts that look like bugs

| Check | Result |
|---|---|
No credentials | `401` |
Wrong token | `401` |
Valid `X-Admin-Token` | `200` |
`/v1/admin/auth/me` with a token | `401` — session-only by design, and the reference now says so |
Bad login | `401`, **flat** envelope: `{"error": "invalid_credentials", "message": "..."}` |
Unknown email to `password/forgot` | `200` with the same message as a known one — no account enumeration |
`model/reload` with no token | `401`, **nested** envelope: `{"error": {"code": "unauthorized", ...}}` |

The last two rows are the same run demonstrating **both error envelopes**. `api.py` returns nested;
the three admin blueprints return flat. A client that parses one shape everywhere breaks on half
the surface. This is documented rather than unified, because unifying it is a breaking change to
whatever already reads the current shapes.

### Moderation levels — written, then put back

The live instance has nine surfaces configured, `post` at `level_1`/`0.1` and `article` at
`level_5`/`0.5` — unlike the empty test fixture, which is worth knowing before reading any
threshold in this report as a default.

1. `PUT /v1/admin/moderation-levels/group_post` `{"level": "strict"}` → `200`, `reject_min: 0.25`
2. The override is reported back in the table, and **`post` did not move with it**
3. The same borderline sentence then scored differently per surface:
   `group_post reject_min=0.25` vs `post reject_min=0.1` — the override is actually in the
   decision path, not merely stored
4. `DELETE` → `200 {"had_override": true, "cleared": true}`
5. The whole `features` table compared byte-for-byte against the copy taken before step 1 —
   **identical**

Rejections: unknown surface → `404 no_such_surface`; unknown level → `400 invalid_request`;
unknown field (`strictness` instead of `level`) → `400 invalid_request`.

### Settings view and audit log

`GET /v1/admin/moderation-settings` reports **11 surfaces**, both new ones among them, with
`mixed: true` (they do not all agree, which is true — `article` sits at `level_5`).

`GET /v1/admin/moderation-logs` → `200`, 8,390 rows, `limit: 50`,
`stats: {allow: 4879, reject: 3499, revise: 12, total: 8390}`. `limit=5` honoured. Filtering by
`surface=group_post` accepted. Unknown `request_id` → `404`.

`GET /v1/admin/moderation-logs/export` → `200`, 3,411,495 bytes,
`Content-Type: text/plain; charset=utf-8` — **one** charset, which is the fix in
[section 6](#6-defects-found).

### Blocked clients — written, then put back

`203.0.113.7` was used, a TEST-NET-3 address reserved for documentation. Not `127.0.0.1`: blocking
the loopback address on the instance the test is talking to would have ended the run.

`POST` → `201` · appears in the list · `DELETE` → `200 {"unblocked": true, "count": 1}` · list
back to its starting state · `not-an-address` → `400`.

---

## 6. Defects found

### D1 — `getModerationLogs` documented the wrong array name *(spec)*

The spec said the rows come back as `items`. They come back as **`logs`**. A client generated from
the spec would have read an absent key, got an empty list, and reported that there were no
moderation decisions to review. Fixed in `openapi.yaml`; the reference now states it in bold.

### D2 — `listImages` promised a `total` that does not exist *(spec)*

The spec put `total` at the top level. It is `pagination.total`. `filtered` and `stats` were
absent from the spec entirely, though both are returned and both are what the dashboard's counts
are built from. Fixed, with an explicit note that there is no top-level `total`.

### D3 — the audit export declared its charset twice *(service)*

```
Content-Type: text/plain; charset=utf-8; charset=utf-8
```

`MEDIA_TYPES["txt"]` in [log_export.py](../flask_api/app/log_export.py) carried
`; charset=utf-8`, and Flask appends its own to any `text/*` mimetype. Browsers shrug at it, which
is why it survived — and `test_log_export.py` asserts on `response.mimetype`, which is the type
*without* parameters, so the unit test could not see it either. **This is the one defect in this
report that a reader of the source could not have found; it took looking at the raw response.**

Fixed by dropping the parameter and letting Flask add the one, with a comment so the
obvious-looking addition does not come back.

### D4 — `policy/preview` had no documented request body *(reference)*

The key is `prompts`. Sending `phrasings` — the word the labels file itself uses for the same
thing — returns `400 missing_prompts`. The spec had it right; the reference had no field table at
all, so anyone working from the reference would have guessed, and the plausible guess is wrong. A
field table was added.

### D5 — a PDF page's visual refusal named the wrong rule *(service)*

Found in part 6. `api.py`'s PDF branch passed an explicit `rule_id` built from the
best-matching **label**, which skipped the cause-naming the image path uses — `refused_by()`,
guarded by `if not rule_id:`. A page refused by exemplar #3672 was logged as **`visual.safe`**:
a rule that does not exist, naming the safe label as the reason a thing was refused. The
category translation was skipped with it, so the author got the generic "breaks community
policies" where an image would have been told which policy.

The comment immediately above that helper describes this exact bug being found and fixed for
images — "an audit row saying a picture was blocked by the safe rule is worse than no audit
row". It was still live for PDFs. `PageVisual` in `pdf.py` also dropped `exemplar_match`, so
the cause was gone before it reached the helper; fixing it properly means carrying the cause
through that struct.

**Not fixed — removed.** Together with D6 this is why PDF extraction came out instead of being
patched. The diagnosis is kept in `private_notes/pdf_on_hold/README.md` for when it returns.

### D6 — one exemplar refuses ordinary business documents *(policy data)*

Exemplar #3672, category `parties`, matches flat document-like pages above the 0.60 bar. Eight
ordinary business documents rendered as scanned pages:

```
 ex_block  ex_allow     cause  REFUSED  document
   0.6945    0.6203  exemplar     True  blank page
   0.6668    0.6078  exemplar     True  price list
   0.6338    0.5863  exemplar     True  supplier profile
   0.6338    0.5901  exemplar     True  spec sheet
   0.6235    0.5433  exemplar     True  letter
   0.5947    0.5116    policy     True  dense paragraph
   0.5878    0.5240      None    False  invoice
   0.5740    0.5416      None    False  certificate
                                 6 of 8 refused
```

A **blank page** is the strongest match in the set. For contrast, 30 real photographs drawn at
random from the dataset: only 2 cross the bar. So the exemplar mechanism is not misfiring
generally — it is specific to flat, low-detail images, which is what every page of a scan is,
and also what the two synthetic PNGs in part 2 were.

This is the same mechanism as [section 7](#7-what-this-run-did-not-settle) seen from the other
side, and it is **not fixed here**: #3672 is a mark in `visual_policy.yaml`, which is admin
work. The options are to drop that mark, raise `exemplar_reject_min` above 0.70, or skip the
visual check on pages whose OCR text was readable.

### Four assertions in the test scripts were wrong, not the service

Recorded because each one looked like a defect first: expecting `400` for oversized content when
the documented and correct answer is `413`; asserting `post` would be absent from the levels table
after setting `group_post`, which ignored that the live instance already has nine surfaces
configured; reading `images` instead of `items` from the image list; and filtering it with
`status` instead of `action`. The service and the documents were right in all four cases — the
third and fourth are what led to D2.

---

## 7. What this run did not settle

**The visual check refuses pictures its own teaching data marks allowed.**

Twenty images were drawn at random (seed `7`) from the admin dataset filtered to
`action=allow` — pictures the dataset exists to teach "this is fine" — fetched through
`GET /v1/admin/images/{id}/file`, and resubmitted to the public `POST /v1/moderate/media`:

```
allowed by the service : 14
rejected by the service:  6

  id=350    dataset=physique   label=physique     0.9274  visual.exemplar#1365
  id=2227   dataset=safe       label=weapon       0.8071  visual.weapon
  id=1525   dataset=weapon     label=weapon       0.9879  visual.weapon
  id=2258   dataset=safe       label=manhandling  0.7802  visual.manhandling
  id=1840   dataset=weapon     label=manhandling  0.8444  visual.manhandling
  id=1456   dataset=weapon     label=weapon       0.9992  visual.weapon

agreement: 14/20 = 70%     probe's own last reported accuracy: 0.881
```

Four of the six are at least arguable: the dataset's own `category` is `weapon` or `physique`, and
a human marked them allowed as a judgement call about context. **`id=2227` and `id=2258` are
not** — their category is `safe`, and they were refused as `weapon` (0.8071) and `manhandling`
(0.7802). Those are plain false positives.

The two synthetic fixtures from part 2 were also refused, both as `violence_or_unrest` through
`visual.exemplar#3672`, which suggests exemplar similarity is loose on out-of-distribution
pictures — a hazard specifically for whatever a real member uploads that does not look like the
training set.

**This is a model-accuracy finding, not an API defect**, and it is out of scope for the commit this
report belongs to. It is in the report because it is the finding most likely to refuse a real
listing once this is on the AI/ML server. The labels endpoint's own `balance.warning` says how to
approach it:

> Measured at 88 against 85, adding twelve allowing phrasings moved the evaluation by four fewer
> wrong refusals and eight fewer correct ones — so measure with `scripts/eval_clip_images.py`
> before and after rather than adding wordings until the number goes green.

The script it means is [flask_api/scripts/eval_clip_images.py](../flask_api/scripts/eval_clip_images.py).
That is the next step for this, and it is a measurement job, not a tuning one.

---

## 8. Part 6 — the PDF path (27/29)

Run after the first 91 checks, because three checks over two one-page documents in part 2 was
not coverage of what `app/pdf.py` claimed to do. Fixtures: a four-page document with the abuse
on page 3, a paragraph broken across PDF line wraps, a password-protected file, a truncated
file, a 101-page file, a 210,000-character file, an image-only scan of abusive text, a scan of
an ordinary supplier profile, a scan with nothing readable on it, a two-page file where only
page 2 is a scan, and a text file named `.pdf`.

### What held up

| Claim | Result |
|---|---|
Abuse on page 3 of 4, not page 1 | rejected, `abuse.targeted` at offsets 198–207 and 208–213 |
Offsets map to the right page | page spans `1:0-82 2:84-152 3:154-283 4:285-355` — both offsets land in page 3 |
A paragraph broken across visual lines | still caught, 0.9996 — `_unwrap` rejoined it |
Abuse that exists only as pixels | OCR read it at 0.9941 confidence, `source: ocr`, refused |
A scan nothing can read | `422 pdf_text_not_extractable`, naming page 1 |
One scanned page in a readable document | `422`, naming **page 2**, not page 1 |
Password protected | `422 pdf_encrypted` |
Truncated mid-file | `422 pdf_unreadable` — and the message is for the author, not the parser's class name |
Text file named `.pdf` | `422 media_unsupported` |
101 pages | `413 pdf_too_many_pages`, `{limit: 100, pages: 101}` |
210,000 characters | `413 pdf_text_too_long`, `{limit: 100000}` |
`/pdf` and `/media` | identical answers on all three cases tried |

Extraction was **good**. The page-offset mapping, the paragraph unwrapping and the
fail-closed rule for unreadable pages all did exactly what the module docstring claimed.

### The two failures

**One was the test's fault.** It read `feedback.issues[].words` to find the flagged offsets,
but a rule-decided issue carries `start`/`end` — `words` is the model path's shape. The page
mapping was correct; the assertion was looking at the wrong key.

**The other was real, and ended the feature.** `scan_clean.pdf` — a scan of an ordinary
supplier profile, OCR-readable, clean words — came back `200 reject`, rule **`visual.safe`**,
no category. That is D5 and D6 in [section 6](#6-defects-found).

Confirming it meant calling the visual check in-process, which also showed why the live server
and a bare `load_settings()` disagree: exemplars are loaded from `exemplars.npz` by
`reload_exemplars()`, so a fresh load scores nothing against them and reports
`exemplar_block: 0.0`. With them loaded:

```
scan_clean.pdf    best label: safe (0.7007)
                  exemplar block 0.6162  allow 0.5904   match #3672 (parties)
                  refused_by: exemplar
                  image path would log: visual.exemplar#3672
                  PDF path logged:      visual.safe
```

### Why this did not come out in the unit tests

`test_visual_check.py` had a test for a weapon inside a scanned PDF, and it asserted
`rule_id == ["visual.weapon"]`. It passed. The stub's label was *already* `weapon`, so naming
the rule from the label and naming it from the cause produce the same string — the test could
not tell the two apart. A replacement needs a stub whose label and cause **disagree**: an
exemplar match sitting under a safe top label, which is precisely the real case.

### What shipped instead

PDF extraction was removed rather than patched: `app/pdf.py`, `tests/test_pdf.py`, the five
scanned-PDF tests, the `/v1/moderate/pdf` route and the `pdf:` settings block. A PDF now
follows the video rule with the opposite default — `media.allow_unchecked_pdf: true` publishes
it with `checked: false`, `visual_content_checked: false` and `text_found: false`.

**That is fail-open**, and it is the one decision in this report a reader should push back on
if they disagree: a scam brochure now publishes unread. The argument for it is that the
alternative was turning away every scanned document, including the six in eight above, and that
`checked: false` keeps the audit row honest about what happened. Set the flag to `false` to
refuse PDFs instead.

---

## 9. Endpoints deliberately not fired

Each of these either costs real time, writes data that cannot be put back, or belongs to work
in flight. They are **untested by this run** and need their own pass before the AI/ML deploy:

| Endpoint | Why not |
|---|---|
`DELETE /v1/admin/moderation-logs` | Destroys the audit trail — 8,390 rows on this instance |
`POST /v1/admin/images/train` | 40+ minutes, and leaves a new model bundle behind |
`POST /v1/admin/images/load-all` | Loads the full dataset; minutes, and changes the active selection |
`POST /v1/admin/images/preset` | Rewrites the category selection |
`POST /v1/admin/images/upload`, `/batch` | Writes into the dataset |
`POST /v1/admin/policy/reset` | Discards the admins' own label work |
`PUT`/`DELETE` on `policy/labels`, `policy/exemplars` | The uncommitted labels in `visual_policy.yaml` are live admin work |

The read side of every one of these was exercised. `policy/preview` was run and confirmed to
change nothing — the labels file was compared before and after and was identical.

---

## 10. Reproducing this

The run used six throwaway scripts outside the repository, one per part, each a flat list of
`check(name, condition)` calls over `requests`. They are not committed: they hard-code a local
base URL and read `ADMIN_TOKEN` out of `flask_api/.env`, and a test that reads a secret from a
developer's machine should not be something CI can be pointed at by accident.

The PDF ones are the exception worth keeping: `t6_pdf.py` and the fixture builder that feeds it
are saved in `private_notes/pdf_on_hold/` with the module they tested, because whoever picks PDF
back up will want the 29 checks that found what section 8 records rather than writing them
again.

To redo it:

```bash
cd flask_api && python wsgi.py          # 127.0.0.1:8000
```

Then drive it from `/docs` by hand, or from a script with `X-Admin-Token` taken from your own
`.env`. The parts that are worth keeping as automated tests already are: the eleven surfaces, the
feedback nouns and the settings-view surface list are covered by
[test_new_surfaces.py](../flask_api/tests/test_new_surfaces.py),
[test_feedback.py](../flask_api/tests/test_feedback.py) and
[test_per_surface_levels.py](../flask_api/tests/test_per_surface_levels.py), and the docs routes by
[test_api.py](../flask_api/tests/test_api.py).

What is **not** worth moving into `pytest`, and should stay a manual pass before each deploy:
anything whose value is that it crosses the WSGI boundary — the raw headers, the multipart
parsing, the 16 MB rejection — and anything that needs the real model loaded, because the unit
suite deliberately runs without it.

Part 6 is the argument for that last clause. The unit suite had a test for a refused scanned
page and it passed, because it stubbed the visual check with a label that happened to match the
real cause. The defect only appeared against the loaded model, the loaded exemplars and a
document that looked like a document — none of which a stub provides.
