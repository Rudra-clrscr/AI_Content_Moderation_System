# AI Content Moderation System — BONC Network MVP

Screens business profiles, product listings, posts and ads for policy violations before they go live.

Stack: DeBERTa-v3-small (ONNX INT8) + Flask + SQL Server (Celery/Redis for async).

## Tracks

| Track | Owner | Folder |
|---|---|---|
| NLP training & ONNX export | NLP Training & Optimization Lead | _TBD_ |
| Flask API: regex gate, inference, threshold routing | AI Backend & API Lead | [flask_api/](flask_api/) |
| SQL Server schema & async pipeline | Data Systems & Integration Lead | _TBD_ |

Interfaces between the tracks are documented in [contracts/](contracts/).
Please work on feature branches and merge through pull requests.
