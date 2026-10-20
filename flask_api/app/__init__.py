"""Flask app factory for the BONC moderation API (Layer 1 gate + ONNX model + routing)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from flask import Flask

from app.config import Settings, load_settings
from app.feedback import Feedback
from app.gate import Gate
from app.levels import ModerationLevels
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
    levels: ModerationLevels
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
    admin_store=None,
    mailer=None,
    mail_async: bool = True,
    system_store=None,
) -> Flask:
    """`scorer`, `sink`, `enqueue`, `fetch_result` are injection points for tests
    and for the data layer's integration; production uses the defaults."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = settings or load_settings()
    app = Flask(__name__, static_folder=None)  # only /demo serves a file, and only when enabled
    # bytes; UTF-8 worst case + JSON overhead, or a PDF upload plus its multipart envelope.
    json_limit = settings.max_chars * 4 + 4096
    app.config["MAX_CONTENT_LENGTH"] = max(json_limit, settings.pdf.max_bytes + 8192,
                                           settings.media.max_bytes + 8192) \
        if settings.pdf.enabled else json_limit

    models = ModelRegistry()
    if scorer is not None:
        models.set(scorer)
    elif settings.mode == "sync":
        # Load once at startup. On failure keep serving: /ready and /v1/moderate return 503
        # until an admin reload succeeds, instead of crash-looping the process.
        try:
            models.load(settings.model_backend, settings.model_dir, **settings.model_kwargs())
        except Exception:
            pass  # already logged with traceback in ModelRegistry.load

    if settings.mode == "async" and enqueue is None:
        from app.tasks import celery_enqueue, celery_fetch_result
        enqueue, fetch_result = celery_enqueue(settings), celery_fetch_result(settings)

    app.extensions["moderation"] = Services(
        settings=settings,
        levels=ModerationLevels.from_yaml(settings.moderation_levels_file),
        pipeline=Pipeline(Gate.from_yaml(settings.gate_patterns_file), models,
                          settings.thresholds, settings.latency_budget_ms, settings.targeted,
                          Feedback.from_yaml(settings.feedback_messages_file), settings.sentence_scan,
                          settings.word_scan, settings.triage, settings.grievance),
        models=models,
        sink=sink or build_sink(settings),
        enqueue=enqueue,
        fetch_result=fetch_result,
    )

    # Admin accounts, password recovery and the saved moderation settings. Saved settings are
    # re-applied here so a restart changes nothing.
    from app.admin_auth import init_admin, restore_levels
    admin = init_admin(app, admin_store, mailer, mail_async)
    restore_levels(app.extensions["moderation"].levels, admin.store)

    from app.system_store import SystemStore
    from app.admin_images import admin_images_bp
    from app.admin_policy import admin_policy_bp, snapshot_default_policy
    sys_store = system_store or SystemStore()
    app.extensions["system_store"] = sys_store
    app.register_blueprint(admin_images_bp)
    app.register_blueprint(admin_policy_bp)
    # Take the pristine copy of the visual policy at startup, before any admin can edit it.
    # Doing it here rather than on the first write is the difference between a default that
    # reflects what shipped and one that reflects whatever the first editor happened to save.
    snapshot_default_policy(settings)
    # Exemplars live in a file beside prompts.npz, so the visual check picks them up without
    # knowing the admin database exists. Loaded here because _load() is lazy and the first
    # upload should not be the thing that discovers a missing file.
    if hasattr(settings.visual, "reload_exemplars"):
        settings.visual.reload_exemplars()

    active_mode = sys_store.get_setting("active_policy_mode", "standard")
    if active_mode == "child_products" and hasattr(settings.visual, "reload_probe"):
        settings.visual.reload_probe(threshold_overrides={"minor": 1.01})

    from app.api import bp
    app.register_blueprint(bp)
    return app
