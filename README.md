# AI Content Moderation System — BONC Network MVP

Screens business profiles, product listings, posts and ads for policy violations before they go live.

Stack: DeBERTa-v3-small (ONNX INT8) + Flask + SQL Server (Celery/Redis for async).

## Tracks

| Track | Owner | Folder |
|---|---|---|
| NLP training & ONNX export | NLP Training & Optimization Lead | _TBD_ |
| Flask API: regex gate, inference, threshold routing | AI Backend & API Lead | [flask_api/](flask_api/) |
| SQL Server schema & async pipeline | Data Systems & Integration Lead | _TBD_ |

## Live demo

To run the service on your own computer and demonstrate it in a browser:

1. Set up and start the server with the guide for your terminal:
   [PowerShell](flask_api/DEMO_GUIDE_POWERSHELL.md) (prompt starts with `PS`) or
   [Command Prompt](flask_api/DEMO_GUIDE_CMD.md).
2. Run the demo with [flask_api/DEMO_GUIDE.md](flask_api/DEMO_GUIDE.md): a 10-minute
   run of show and a bank of tested example sentences.

Interfaces between the tracks are documented in [contracts/](contracts/).
Please work on feature branches and merge through pull requests.
