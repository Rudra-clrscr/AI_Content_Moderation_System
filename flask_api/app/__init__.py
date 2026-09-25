"""Flask app factory for the BONC moderation API (Layer 1 gate + ONNX model + routing)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from flask import Flask

from app.config import Settings, load_settings
from app.gate import Gate
from app.model import ModelRegistry, Scorer
from app.pipeline import ModerationRequest, Pipeline
from app.sinks import ResultSink, build_sink

log = logging.getLogger(__name__)

# (request) -> None. Enqueues model inference for async mode.
Enqueue = Callable[[ModerationRequest], None]
# (request_id) -> result dict, or {"status": "pending"|"failed", ...}
FetchResult = Callable[[str], dict]


@dataclass
class Services:
    settings: Settings
    pipeline: Pipeline
    models: ModelRegistry
    sink: ResultSink
    enqueue: Enqueue | None = None
    fetch_result: FetchResult | None = None


def create_app(
    settings: Settings | None = None,
    *,
    scorer: Scorer | None = None,
    sink: ResultSink | None = None,
    enqueue: Enqueue | None = None,
    fetch_result: FetchResult | None = None,
) -> Flask:
    """`scorer`, `sink`, `enqueue`, `fetch_result` are injection points for tests
    and for Intern 3's integration; production uses the defaults."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = settings or load_settings()
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = settings.max_chars * 4 + 4096  # bytes; UTF-8 worst case + JSON overhead

    models = ModelRegistry()
    if scorer is not None:
        models.set(scorer)
    elif settings.mode == "sync":
        # Load once at startup. On failure keep serving: /ready and /v1/moderate return 503
        # until an admin reload succeeds, instead of crash-looping the process.
        try:
            models.load(settings.model_backend, settings.model_dir,
                        intra_op_threads=settings.intra_op_threads,
                        inter_op_threads=settings.inter_op_threads)
        except Exception:
            pass  # already logged with traceback in ModelRegistry.load

    if settings.mode == "async" and enqueue is None:
        from app.tasks import celery_enqueue, celery_fetch_result
        enqueue, fetch_result = celery_enqueue(settings), celery_fetch_result(settings)

    app.extensions["moderation"] = Services(
        settings=settings,
        pipeline=Pipeline(Gate.from_yaml(settings.gate_patterns_file), models,
                          settings.thresholds, settings.latency_budget_ms),
        models=models,
        sink=sink or build_sink(settings),
        enqueue=enqueue,
        fetch_result=fetch_result,
    )

    from app.api import bp
    app.register_blueprint(bp)
    return app
