# Contract: Moderation API and result payload (Intern 2 → Intern 3)

**Status:** DRAFT. Proposed by Intern 2 and needs Intern 3's sign-off.
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
| `content` | yes | A non-empty string of at most `limits.max_chars` characters (default 10,000). |
| `content_type` | yes | One of `business_profile`, `product_listing`, `post`, `advertisement`. |
| `content_id` | no | The platform's own ID, as a string or an integer. It's always returned as a string. |

## Responses

**`200` final result.** This comes back in sync mode, or in either mode when the
Layer 1 gate blocks the content:

```json
{
  "schema_version": "1.0",
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
  "gate_version": 1,
  "model_version": "deberta-v3-small-int8-2026.10.01",
  "thresholds": {"allow_below": 0.3, "reject_at": 0.85},
  "latency_ms": {"gate": 0.02, "inference": 11.4, "total": 11.6},
  "decided_at": "2026-09-25T14:03:11.204+00:00"
}
```

| Field | Notes |
|---|---|
| `decision` | `allow` / `review` / `reject` |
| `decided_by` | `gate` means Layer 1 blocked it. `risk_score`, `predicted_label`, `label_scores`, `model_version` and `latency_ms.inference` are then `null`. `model` means the model's score decided it. |
| `gate_matches` | A list of `{rule_id, category, action}`. A `flag` match raises the decision to at least `review`. |
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

## Who writes to SQL Server: proposal

Whichever process produces the **final** result calls `sink.emit(result)` exactly once:

- In sync mode, and for gate blocks, that's the Flask process.
- In async mode, when the model runs, that's the Celery worker (`app/tasks.py::run_moderation`).

Intern 3 would implement a `ResultSink`, for example a `SqlServerSink` or an
"enqueue a DB-write task" sink, and it plugs in without changing the API code.
Today the default `LoggingSink` just writes JSON lines, with `content` left out.

## Open questions for Intern 3

1. Should the sink write to SQL Server synchronously, or enqueue a separate write task so the API latency doesn't include the DB write?
2. Should raw `content` be stored, or only `content_sha256` plus a reference to the platform's own content table?
3. Should `request_id` be the primary key of the moderation-events table? It's a UUID4 string today.
4. Queue and broker names: I've assumed queue `moderation` and Redis DBs 0 (broker) and 1 (results). Are those right?
5. Do the four `content_type` values match your schema's enum?
