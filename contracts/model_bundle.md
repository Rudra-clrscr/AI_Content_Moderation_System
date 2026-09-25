# Contract: Model bundle (Intern 1 → Intern 2)

**Status:** DRAFT. Proposed by Intern 2 and needs Intern 1's sign-off.
**Consumer:** `flask_api/app/model.py` (`OnnxScorer`)

Each model release is one directory. The Flask service loads it from `MODEL_DIR`
(default `flask_api/models/current`) at startup, or on `POST /v1/admin/model/reload`.

```
<model_dir>/
  model.onnx         # INT8-quantized ONNX export
  tokenizer.json     # HF *fast* tokenizer file (tokenizer.save_pretrained() writes it)
  model_meta.json    # see below
```

## model.onnx

| | Requirement |
|---|---|
| Inputs | Any subset of `input_ids`, `attention_mask`, `token_type_ids`. All `int64`, shape `[batch, seq]`. |
| Sequence axis | Dynamic (preferred). If it's fixed, set `pad_to_max_length: true` in the meta. |
| Output | Raw **logits** (not probabilities), shape `[batch, num_labels]`. The first output is used unless `output_name` is set. |
| Execution provider | Must run on `CPUExecutionProvider`. |

## model_meta.json

```json
{
  "version": "deberta-v3-small-int8-2026.10.01",
  "labels": ["safe", "spam", "fraud", "hate", "adult"],
  "safe_label": "safe",
  "max_length": 256,
  "activation": "softmax",
  "pad_to_max_length": false
}
```

| Field | Required | Meaning |
|---|---|---|
| `version` | yes | Unique ID. It's echoed as `model_version` in every result and logged by Intern 3. |
| `labels` | yes | Label names in logit order. |
| `safe_label` | yes | The "no violation" label. It must appear in `labels`. |
| `max_length` | no (256) | The truncation length used in training. |
| `activation` | no (`softmax`) | `softmax` for a single-label classifier, `sigmoid` for a multi-label one. |
| `pad_to_max_length` | no (false) | Set to true only if the export has a fixed sequence length. |
| `output_name` | no | The output tensor to read, if it isn't the first one. |

Any extra keys (training date, eval metrics, and so on) are kept but ignored.

## How the risk score is computed

- Single logit: `risk = sigmoid(logit)`
- `softmax`: `risk = 1 − P(safe_label)`
- `sigmoid`, several labels: `risk = max P(label)` over the non-safe labels

The Flask thresholds (`allow_below`, `reject_at`) apply to this `risk` value. When the
model is retrained, please send recommended thresholds along with it, for example the
values that hit the target precision and recall on the validation set.

## Preprocessing: please confirm

The Flask layer applies **no** text preprocessing before tokenization. It passes the
raw text to `tokenizer.json`, truncates to `max_length`, and adds the special tokens
the tokenizer defines. If training did anything else (lowercasing, stripping URLs,
joining a title and a body, and so on), tell Intern 2 so the two stay identical.

## Verifying a bundle before handing it over

```
cd flask_api
MODEL_DIR=/path/to/bundle python scripts/bench_latency.py
```

If the bundle loads, this prints p50/p95/p99 latency. It exits non-zero if the p95
inference latency is over the 30 ms budget.
