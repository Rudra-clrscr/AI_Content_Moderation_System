"""Async mode: Celery task that runs model inference in a worker.

Flask runs the gate inline, then enqueues `moderation.moderate` with
task_id == request_id. The worker loads the model once per process, runs the
same Pipeline as sync mode, and hands the result to the ResultSink.

Start a worker (from flask_api/):
    celery -A app.tasks:celery_app worker -Q moderation --concurrency=4 --pool=prefork
    (on Windows dev boxes use --pool=solo)

NOTE: queue names, broker and who writes to SQL Server are to be confirmed with
Intern 3 — if their pipeline owns the worker, they can import `run_moderation`
directly and ignore this module.
"""
from __future__ import annotations

import logging

from celery import Celery
from celery.signals import worker_process_init

from app.config import Settings, load_settings
from app.gate import Gate
from app.model import ModelRegistry
from app.pipeline import ModerationRequest, Pipeline
from app.sinks import LoggingSink, ResultSink, SqlServerSink

log = logging.getLogger(__name__)
TASK_NAME = "moderation.moderate"
PERSIST_TASK_NAME = "moderation.persist_result"

_settings = load_settings()
celery_app = Celery("moderation", broker=_settings.celery_broker_url, backend=_settings.celery_result_backend)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,            # redeliver if a worker dies mid-task
    worker_prefetch_multiplier=1,   # inference is CPU-bound; don't hoard tasks
    result_expires=24 * 3600,
)

_pipeline: Pipeline | None = None
# Unwrapped on purpose: a failed write is retried by persist_result_task below.
_sink: ResultSink = SqlServerSink(_settings) if _settings.result_sink == "sql" else LoggingSink()


@worker_process_init.connect
def _load_model(**_):
    global _pipeline
    s = _settings
    models = ModelRegistry()
    models.load(s.model_backend, s.model_dir, **s.model_kwargs())
    _pipeline = Pipeline(Gate.from_yaml(s.gate_patterns_file), models, s.thresholds, s.latency_budget_ms)


def run_moderation(payload: dict) -> dict:
    """Pure function the task wraps — callable from Intern 3's own worker if preferred."""
    if _pipeline is None:
        _load_model()
    result = _pipeline.moderate(ModerationRequest.from_dict(payload))
    try:
        _sink.emit(result)
    except Exception:
        # Don't fail (and re-run inference for) the moderation task over a storage error:
        # hand the finished result to a separate task that retries just the write.
        log.exception("db write failed for %s, scheduling retry", result["request_id"])
        persist_result_task.apply_async(args=[result], countdown=5, queue=_settings.celery_queue)
    return result


@celery_app.task(name=TASK_NAME, autoretry_for=(ConnectionError,), retry_backoff=True, max_retries=3)
def moderate_task(payload: dict) -> dict:
    return run_moderation(payload)


@celery_app.task(name=PERSIST_TASK_NAME, autoretry_for=(Exception,), retry_backoff=True,
                 retry_backoff_max=600, max_retries=8)
def persist_result_task(result: dict) -> None:
    _sink.emit(result)


def celery_enqueue(settings: Settings):
    def enqueue(req: ModerationRequest) -> None:
        moderate_task.apply_async(args=[req.as_dict()], task_id=req.request_id, queue=settings.celery_queue)
    return enqueue


def celery_fetch_result(settings: Settings):
    def fetch(request_id: str) -> dict:
        res = celery_app.AsyncResult(request_id)
        if res.successful():
            return res.result
        if res.failed():
            return {"request_id": request_id, "status": "failed", "error": str(res.result)}
        # Celery can't distinguish "queued" from "unknown id" — both are PENDING.
        return {"request_id": request_id, "status": "pending"}
    return fetch
