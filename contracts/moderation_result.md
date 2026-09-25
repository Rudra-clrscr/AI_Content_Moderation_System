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

## Open questions for Intern 3

1. In sync mode the insert still happens inside the request. Should it move to a background writer or queue so API latency doesn't include the DB round-trip?
2. ~~Should raw `content` be stored?~~ Settled: hash only.
3. ~~Should `request_id` be the primary key?~~ Settled: yes.
4. Queue and broker names: I've assumed queue `moderation` and Redis DBs 0 (broker) and 1 (results). Are those right?
5. Do the four `content_type` values match your schema's enum?
