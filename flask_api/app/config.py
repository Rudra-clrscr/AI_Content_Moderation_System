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
    DEMO_PAGE             1 to serve the demo UI at /demo (off by default)
    TARGETED_ABUSE        0 to turn off per-sentence scoring of text aimed at someone
    SENTENCE_SCAN         0 to turn off scoring every sentence on its own
    WORD_SCAN             0 to turn off word windows and word-by-word trigger analysis
    TRIAGE                0 to turn off the linear pre-filter (every span then goes to the model)
    TRIAGE_THRESHOLD      float, overrides the threshold chosen when the filter was trained
    PDF_UPLOAD            0 to turn off POST /v1/moderate/pdf and /v1/moderate/media
    MEDIA_UPLOAD          0 to turn off image/video attachment checking
    PDF_OCR               0 to turn off OCR of scanned pages (they are then refused, as before)
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
from app.media import MediaLimits
from app.ocr import OcrConfig, OcrEngine
from app.pdf import PdfLimits
from app.targeted import DEFAULT_SUBJECTS, SentenceScan, TargetedAbuse, WordScan
from app.triage import TriageFilter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS = PROJECT_ROOT / "config" / "settings.yaml"
load_dotenv(PROJECT_ROOT / ".env")

@dataclass
class Settings:
    mode: str = "sync"
    model_backend: str = "onnx"
    model_dir: Path = PROJECT_ROOT / "models" / "v4"
    intra_op_threads: int = 4
    inter_op_threads: int = 1
    label_weights: dict[str, float] | None = None
    thresholds: Thresholds = field(default_factory=lambda: Thresholds(0.30, 0.70))
    gate_patterns_file: Path = PROJECT_ROOT / "config" / "gate_patterns.yaml"
    feedback_messages_file: Path = PROJECT_ROOT / "config" / "feedback_messages.yaml"
    max_chars: int = 25_000
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
    demo_page: bool = False
    targeted: TargetedAbuse = field(default_factory=TargetedAbuse)
    sentence_scan: SentenceScan = field(default_factory=SentenceScan)
    word_scan: WordScan = field(default_factory=WordScan)
    triage: TriageFilter = field(default_factory=TriageFilter)   # disabled unless a bundle is configured
    pdf: PdfLimits = field(default_factory=PdfLimits)
    media: MediaLimits = field(default_factory=MediaLimits)
    ocr: OcrEngine = field(default_factory=lambda: OcrEngine(OcrConfig(enabled=False)))

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
    ta = raw.get("targeted_abuse", {})
    sc = raw.get("sentence_scan", {})
    ws = raw.get("word_scan", {})
    tr = raw.get("triage", {})
    pdf = raw.get("pdf", {})
    ocr = pdf.get("ocr", {})
    med = raw.get("media", {})
    env = os.environ.get
    triage_threshold = env("TRIAGE_THRESHOLD", tr.get("threshold"))
    triage = (TriageFilter.load(_resolve(tr.get("dir", "models/triage-v1")),
                                None if triage_threshold is None else float(triage_threshold))
              if _as_bool(env("TRIAGE", tr.get("enabled", True))) else TriageFilter())

    return Settings(
        mode=env("MODERATION_MODE", raw.get("mode", "sync")),
        model_backend=env("MODEL_BACKEND", model.get("backend", "onnx")),
        model_dir=_resolve(env("MODEL_DIR", model.get("dir", "models/v4"))),
        intra_op_threads=int(env("MODEL_INTRA_OP_THREADS", model.get("intra_op_threads", 4))),
        inter_op_threads=int(model.get("inter_op_threads", 1)),
        label_weights=model.get("label_weights"),
        thresholds=Thresholds(
            allow_max=float(env("THRESHOLD_ALLOW_MAX", thr.get("allow_max", 0.30))),
            reject_min=float(env("THRESHOLD_REJECT_MIN", thr.get("reject_min", 0.70))),
        ),
        gate_patterns_file=_resolve(raw.get("gate", {}).get("patterns_file", "config/gate_patterns.yaml")),
        feedback_messages_file=_resolve(raw.get("feedback", {}).get("messages_file", "config/feedback_messages.yaml")),
        max_chars=int(raw.get("limits", {}).get("max_chars", 25_000)),
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
        demo_page=_as_bool(env("DEMO_PAGE", raw.get("demo_page", False))),
        targeted=TargetedAbuse(
            enabled=_as_bool(env("TARGETED_ABUSE", ta.get("enabled", True))),
            subjects=tuple(ta.get("subjects") or DEFAULT_SUBJECTS),
            max_segments=int(ta.get("max_segments", 3)),
        ),
        sentence_scan=SentenceScan(
            enabled=_as_bool(env("SENTENCE_SCAN", sc.get("enabled", True))),
            max_sentences=int(sc.get("max_sentences", 400)),
            revise_on_middle=_as_bool(sc.get("revise_on_middle", True)),
        ),
        word_scan=WordScan(
            enabled=_as_bool(env("WORD_SCAN", ws.get("enabled", True))),
            min_contribution=float(ws.get("min_contribution", 0.10)),
            max_triggers=int(ws.get("max_triggers", 5)),
            max_spans=int(ws.get("max_spans", 5)),
            max_words=int(ws.get("max_words", 60)),
            window_words=int(ws.get("window_words", 40)),
            window_stride=int(ws.get("window_stride", 20)),
            deobfuscate=_as_bool(ws.get("deobfuscate", True)),
            max_candidates=int(ws.get("max_candidates", 10)),
            max_calls=int(ws.get("max_calls", 40)),
        ),
        triage=triage,
        pdf=PdfLimits(
            enabled=_as_bool(env("PDF_UPLOAD", pdf.get("enabled", True))),
            max_bytes=int(pdf.get("max_bytes", 10_000_000)),
            max_pages=int(pdf.get("max_pages", 100)),
            max_chars=int(pdf.get("max_chars", 100_000)),
            min_chars_per_page=int(pdf.get("min_chars_per_page", 20)),
            reject_unreadable_pages=_as_bool(pdf.get("reject_unreadable_pages", True)),
        ),
        media=MediaLimits(
            enabled=_as_bool(env("MEDIA_UPLOAD", med.get("enabled", True))),
            max_bytes=int(med.get("max_bytes", 15_000_000)),
            max_pixels=int(med.get("max_pixels", 40_000_000)),
            allow_unchecked_video=_as_bool(med.get("allow_unchecked_video", False)),
            allow_unreadable_image=_as_bool(med.get("allow_unreadable_image", False)),
        ),
        ocr=OcrEngine(OcrConfig(
            enabled=_as_bool(env("PDF_OCR", ocr.get("enabled", True))),
            max_pages=int(ocr.get("max_pages", 10)),
            dpi=int(ocr.get("dpi", 200)),
            max_pixels=int(ocr.get("max_pixels", 4_000_000)),
            min_chars=int(ocr.get("min_chars", 20)),
            min_confidence=float(ocr.get("min_confidence", 0.5)),
            min_chars_per_box=int(ocr.get("min_chars_per_box", 6)),
            trust_short_confidence=float(ocr.get("trust_short_confidence", 0.90)),
            rec_model_path=str(ocr.get("rec_model_path") or ""),
            rec_lang=str(ocr.get("rec_lang") or ""),
        )),
    )


def _as_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")
