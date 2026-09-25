# flask_api: moderation service (Intern 2 track)

This service wraps the DeBERTa-v3-small ONNX model in a Flask API. A request goes
through three steps:

1. **Layer 1 gate.** Regex and term-list rules in [config/gate_patterns.yaml](config/gate_patterns.yaml) can reject content on the spot (`block`) or force a human review (`flag`).
2. **Model.** An ONNX Runtime session is loaded once at startup. It can be hot-swapped through `POST /v1/admin/model/reload`.
3. **Threshold routing.** The risk score becomes `allow`, `review` or `reject`, using the thresholds in [config/settings.yaml](config/settings.yaml).

The interfaces with the other two tracks are documented in [../contracts/](../contracts/).

## Quick start

```bash
cd flask_api
python -m venv .venv && .venv/Scripts/activate      # Windows; on Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q

# The model is committed under models/current via Git LFS (install from https://git-lfs.com,
# then `git lfs install` once). If you cloned before installing LFS, run `git lfs pull`.
python wsgi.py

# To update to Intern 1's latest model (github.com/Gupta35251/BONC), then commit it:
python scripts/fetch_model.py

# Or without a real model (dev only):
MODEL_BACKEND=stub python wsgi.py

curl -X POST localhost:8000/v1/moderate -H "Content-Type: application/json" \
  -d '{"content":"Bulk cotton yarn supplier","content_type":"product_listing","content_id":"LST-1"}'
```

## Endpoints

| Method | Path | |
|---|---|---|
| POST | `/v1/moderate` | Moderates one piece of content. Returns `200` with the result, or `202` pending in async mode. |
| GET | `/v1/moderate/<request_id>` | Looks up a result (async mode only). |
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
  sinks.py      ResultSink: where Intern 3's DB writer plugs in
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
p95 is about 0.1 ms. Real numbers need Intern 1's INT8 DeBERTa export.
