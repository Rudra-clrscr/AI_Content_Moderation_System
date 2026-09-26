# Demo guide

This runs a live demo of the moderation service in about 10 minutes. It uses
the real `deberta-v3-small-int8-bonc-v1` model and needs no database or Redis.

## Before the demo (5 min)

```powershell
cd flask_api
git lfs pull                                   # make sure the real model is downloaded
.venv\Scripts\activate
$env:DEMO_PAGE = "1"; $env:RESULT_SINK = "log"
python -m waitress --port=8000 wsgi:app
```

Open **http://127.0.0.1:8000/demo**. The header chips should read `status ready`,
`model deberta-v3-small-int8-bonc-v1`, `gate rules 6` and `allow ≤ 0.30 · reject ≥ 0.70`.

**Warm up.** Click every example once before the audience arrives, then refresh
the page. The first request after startup is slower.

Each example also has a direct link, `/demo#1` to `/demo#8`, if you'd rather
switch tabs than click.

## Run of show

| # | Click | What happens | What to say |
|---|---|---|---|
| 1 | **ALLOW**: steel valves | allow, risk ≈ 0.00 | Normal B2B content is published immediately, in about 10–15 ms of inference. |
| 2 | **REVIEW**: distributors | review, risk ≈ 0.50 | The model has three outcomes (safe, review, reject). Mid-risk content goes to a human moderator instead of being auto-rejected. |
| 3 | **REVIEW**: crypto payment | review, although the model says safe (0.005) | Why there are two layers: the regex gate flagged off-platform payment, which the model missed, and a flag forces at least a human review. |
| 4 | **REJECT**: bank details and OTP | reject, risk ≈ 1.00 | Phishing is caught by the model. |
| 5 | **REJECT**: threat | reject | Abuse is caught too. |
| 6 | **REJECT**: double your money | reject by the gate, and the pipeline shows the model as skipped | Obvious scams never reach the model. The gate costs about 0.05 ms, so it's a free short-circuit. |
| 7 | **REJECT**: "scumbag" | reject by the gate | The abusive-language blocklist. In production it also loads the policy team's full slur list from a private file. |
| 8 | **REJECT**: paypa1-verify domain | reject by the gate | Known phishing and scam domains are blocked outright. Adding one is a one-line config change, with no retraining. |
| 9 | Type your own text | — | Invite the audience to suggest something. |
| 10 | Expand **Full result payload** | — | This is exactly what the SQL Server audit table stores: score, decision, model and gate versions, and the thresholds used. So every decision can be audited later. |

Finish on the **This session** table: the count of each decision and the mean inference time.

## Optional extras

- **Validation.** Submit empty text: you get a clean `400 invalid_content` error instead of a crash.
- **Resilience.** Restart with `$env:RESULT_SINK = "sql"` and no database configured. Moderation still returns decisions, and the terminal logs `db_write_failed` with the full result so it can be replayed.
- **Latency.** Run `python scripts\bench_latency.py`. It shows p95 inference within the 30 ms budget.
- **Async mode.** Requires Redis and a Celery worker. Start the server with `$env:MODERATION_MODE = "async"`, and the page shows `PENDING` with a status URL.

## Honest caveats, if asked

- The model is v1. Some legitimate business text lands in review (for example "family-run textile mill"), and it misses counterfeit listings ("replica watches"). The ML team is recalibrating it.
- The thresholds (0.3 / 0.7) are placeholders until then.
- The abuse and blocked-domain lists hold a few demo entries. The production slur list is loaded from `config/private/slurs.txt`, which the policy team supplies and which isn't committed to git.
