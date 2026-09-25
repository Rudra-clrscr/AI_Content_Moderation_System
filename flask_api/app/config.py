"""Settings loaded from config/settings.yaml, with env-var overrides.

Env overrides (all optional):
    MODERATION_SETTINGS   path to the settings YAML
    MODERATION_MODE       sync | async
    MODEL_BACKEND         onnx | stub
    MODEL_DIR             directory holding the ONNX model bundle
    MODEL_INTRA_OP_THREADS  ONNX Runtime threads per inference (default 4)
    THRESHOLD_ALLOW_MAX   float, risk <= this -> allow
    THRESHOLD_REJECT_MIN  float, risk >= this -> reject
    CELERY_BROKER_URL / CELERY_RESULT_BACKEND
    ADMIN_TOKEN           enables POST /v1/admin/model/reload when set
    RESULT_SINK           sql | log
    DB_SERVER, DB_NAME    SQL Server host and database
    DB_USER, DB_PASSWORD  SQL auth; if DB_USER is unset, Windows auth (Trusted_Connection) is used
    DB_DRIVER             ODBC driver name (default "ODBC Driver 18 for SQL Server")
    DB_TRUST_SERVER_CERTIFICATE  yes | no (default yes; set no in production with a real cert)
    DB_TIMEOUT_SECONDS    login timeout (default 5)

Values can also come from flask_api/.env (gitignored); real env vars win.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

from app.routing import Thresholds

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS = PROJECT_ROOT / "config" / "settings.yaml"
load_dotenv(PROJECT_ROOT / ".env")

@dataclass
class Settings:
    mode: str = "sync"
    model_backend: str = "onnx"
    model_dir: Path = PROJECT_ROOT / "models" / "current"
    intra_op_threads: int = 4
    inter_op_threads: int = 1
    label_weights: dict[str, float] | None = None
    thresholds: Thresholds = field(default_factory=lambda: Thresholds(0.30, 0.70))
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
    db_driver: str = "ODBC Driver 18 for SQL Server"
    db_trust_server_certificate: bool = True
    db_timeout_seconds: int = 5
    result_sink: str = "sql"

    def model_kwargs(self) -> dict:
        """Keyword args for ModelRegistry.load / OnnxScorer."""
        return {"intra_op_threads": self.intra_op_threads, "inter_op_threads": self.inter_op_threads,
                "label_weights": self.label_weights}

    def __post_init__(self) -> None:
        if self.result_sink not in ("sql", "log"):
            raise ValueError(f"result sink must be 'sql' or 'log', got {self.result_sink!r}")
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
    db = raw.get("database", {})
    env = os.environ.get

    return Settings(
        mode=env("MODERATION_MODE", raw.get("mode", "sync")),
        model_backend=env("MODEL_BACKEND", model.get("backend", "onnx")),
        model_dir=_resolve(env("MODEL_DIR", model.get("dir", "models/current"))),
        intra_op_threads=int(env("MODEL_INTRA_OP_THREADS", model.get("intra_op_threads", 4))),
        inter_op_threads=int(model.get("inter_op_threads", 1)),
        label_weights=model.get("label_weights"),
        thresholds=Thresholds(
            allow_max=float(env("THRESHOLD_ALLOW_MAX", thr.get("allow_max", 0.30))),
            reject_min=float(env("THRESHOLD_REJECT_MIN", thr.get("reject_min", 0.70))),
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
        db_driver=env("DB_DRIVER", db.get("driver", "ODBC Driver 18 for SQL Server")),
        db_trust_server_certificate=_as_bool(
            env("DB_TRUST_SERVER_CERTIFICATE", db.get("trust_server_certificate", True))),
        db_timeout_seconds=int(env("DB_TIMEOUT_SECONDS", db.get("timeout_seconds", 5))),
        result_sink=env("RESULT_SINK", raw.get("result_sink", "sql")),
    )


def _as_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")
