# AI Content Moderation System — BONC Network MVP

Screens business profiles, product listings, posts and ads for policy violations before they go live.

Stack: DeBERTa-v3-small (ONNX INT8) + Flask + SQL Server (Celery/Redis for async).

## Tracks

| Track | Owner | Folder |
|---|---|---|
| NLP training & ONNX export | NLP Training & Optimization Lead | [training/](training/) (bonc-v4: data, synthetic generator, GPU training, calibration, export) |
| Flask API: regex gate, inference, threshold routing | AI Backend & API Lead | [flask_api/](flask_api/) |
| SQL Server schema & async pipeline | Data Systems & Integration Lead | _TBD_ |

## Live demo

To run the service on your own computer and demonstrate it in a browser:

1. Set up and start the server with the guide for your terminal:
   [PowerShell](flask_api/DEMO_GUIDE_POWERSHELL.md) (prompt starts with `PS`) or
   [Command Prompt](flask_api/DEMO_GUIDE_CMD.md).
2. Run the demo with [flask_api/DEMO_GUIDE.md](flask_api/DEMO_GUIDE.md): a 10-minute
   run of show and a bank of tested example sentences.

## Pictures

The service screens images too — weapons, gore, weddings, gym selfies, children as the subject.
That model is separate from the text one and **two of its files are not in git**, so a fresh
clone checks text and silently publishes pictures unlooked-at until you run two commands:
[flask_api/SETUP_IMAGE_MODEL.md](flask_api/SETUP_IMAGE_MODEL.md).

The policy itself is [flask_api/config/visual_policy.yaml](flask_api/config/visual_policy.yaml)
— plain YAML, tracked, reviewable in a pull request.

Interfaces between the tracks are documented in [contracts/](contracts/).
Please work on feature branches and merge through pull requests.
