# Swagger / API test report

**Run date:** 2026-11-01 · **Commit:** `fd09080` (`feature/api-contract-docs-and-two-new-surfaces`)
**Result:** 91 live HTTP checks, 91 passed · 932 unit tests, 932 passed · 0 spec-to-code drift

This is the acceptance run for [openapi.yaml](openapi.yaml) and [API_REFERENCE.md](API_REFERENCE.md):
the two documents say what the service does, and this is the record of checking that claim
against a running instance rather than against the source.

Four defects were found. All four are fixed in the commit above. One of them was in the
service, three were in the documents — which is the point of running this at all, because a
wrong document is a defect that ships inside every client written against it.

One finding is **not** fixed and is the reason to read [section 7](#7-what-this-run-did-not-settle):
the visual check refuses roughly one in five pictures its own teaching data marks allowed.

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
Spec | OpenAPI 3.0.3, 43 paths, **50 operations**, 8 tags |
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
| | **Total** | **91** | **91 pass** |

Separately: `pytest flask_api/tests` → **932 passed** in 3m 50s, and a static check that parses the
blueprint decorators and diffs them against the spec → **50 operations in the code, 50 in the
spec, no drift**, with the `ContentType` enum at 11 values in both documents in the same order.

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
extractable text, not against a library's output.

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

## 8. Endpoints deliberately not fired

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

## 9. Reproducing this

The run used four throwaway scripts outside the repository, one per part, each a flat list of
`check(name, condition)` calls over `requests`. They are not committed: they hard-code a local
base URL and read `ADMIN_TOKEN` out of `flask_api/.env`, and a test that reads a secret from a
developer's machine should not be something CI can be pointed at by accident.

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
