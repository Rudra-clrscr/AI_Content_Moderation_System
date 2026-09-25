"""Settings loaded from config/settings.yaml, with env-var overrides.

Env overrides (all optional):
    MODERATION_SETTINGS   path to the settings YAML
    MODERATION_MODE       sync | async
    MODEL_BACKEND         onnx | stub
    MODEL_DIR             directory holding the ONNX model bundle
    THRESHOLD_ALLOW_BELOW float
    THRESHOLD_REJECT_AT   float
    CELERY_BROKER_URL / CELERY_RESULT_BACKEND
    ADMIN_TOKEN           enables POST /v1/admin/model/reload when set
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

import yaml

from app.routing import Thresholds

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS = PROJECT_ROOT / "config" / "settings.yaml"
load_dotenv(PROJECT_ROOT / ".env")

@dataclass
class Settings:
    mode: str = "sync"
    model_backend: str = "onnx"
    model_dir: Path = PROJECT_ROOT / "models" / "current"
    intra_op_threads: int = 1
    inter_op_threads: int = 1
    thresholds: Thresholds = field(default_factory=lambda: Thresholds(0.30, 0.85))
    gate_patterns_file: Path = PROJECT_ROOT / "config" / "gate_patterns.yaml"
    max_chars: int = 10_000
    latency_budget_ms: float = 30.0
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    celery_queue: str = "moderation"
    admin_token: str | None = None
    db_server: str | None = None
    db_name: str | None = None
    db_user: str | None = None
    db_password: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in ("sync", "async"):
            raise ValueError(f"mode must be 'sync' or 'async', got {self.mode!r}")
        if self.model_backend not in ("onnx", "stub"):
            raise ValueError(f"model backend must be 'onnx' or 'stub', got {self.model_backend!r}")


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def load_settings(path: str | Path | None = None) -> Settings:
    path = _resolve(path or os.environ.get("MODERATION_SETTINGS", DEFAULT_SETTINGS))
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    model = raw.get("model", {})
    thr = raw.get("thresholds", {})
    celery = raw.get("celery", {})
    env = os.environ.get

    return Settings(
        mode=env("MODERATION_MODE", raw.get("mode", "sync")),
        model_backend=env("MODEL_BACKEND", model.get("backend", "onnx")),
        model_dir=_resolve(env("MODEL_DIR", model.get("dir", "models/current"))),
        intra_op_threads=int(model.get("intra_op_threads", 1)),
        inter_op_threads=int(model.get("inter_op_threads", 1)),
        thresholds=Thresholds(
            allow_below=float(env("THRESHOLD_ALLOW_BELOW", thr.get("allow_below", 0.30))),
            reject_at=float(env("THRESHOLD_REJECT_AT", thr.get("reject_at", 0.85))),
        ),
        gate_patterns_file=_resolve(raw.get("gate", {}).get("patterns_file", "config/gate_patterns.yaml")),
        max_chars=int(raw.get("limits", {}).get("max_chars", 10_000)),
        latency_budget_ms=float(raw.get("latency_budget_ms", 30)),
        celery_broker_url=env("CELERY_BROKER_URL", celery.get("broker_url", "redis://localhost:6379/0")),
        celery_result_backend=env("CELERY_RESULT_BACKEND", celery.get("result_backend", "redis://localhost:6379/1")),
        celery_queue=celery.get("queue", "moderation"),
        admin_token=env("ADMIN_TOKEN") or None,
        db_server=env("DB_SERVER"),
        db_name=env("DB_NAME"),
        db_user=env("DB_USER"),
        db_password=env("DB_PASSWORD"),
    )
