"""Result sinks: where finished moderation results go.

This is the single hand-off point to the data layer. Whichever process
produces the final result (Flask in sync mode, the Celery worker in async mode)
calls `sink.emit(result)`.

A storage failure must never lose the moderation decision: Flask wraps the SQL
sink in `FailSafeSink`, and the Celery worker retries failed writes in a
separate task (see app/tasks.py).
"""
from __future__ import annotations

import json
import logging
from typing import Protocol

from app.config import Settings
from app.db import get_connection

log = logging.getLogger("moderation.results")

# SQL Server error numbers for primary-key / unique-index violations.
_DUPLICATE_KEY_ERRORS = ("2627", "2601")


class ResultSink(Protocol):
    def emit(self, result: dict) -> None: ...


def _loggable(result: dict) -> str:
    return json.dumps({k: v for k, v in result.items() if k != "content"})


class LoggingSink:
    """One JSON line per result. Content is omitted from logs."""

    def emit(self, result: dict) -> None:
        log.info(_loggable(result))


class SqlServerSink:
    """Writes completed moderation results to dbo.moderation_events.

    Idempotent on request_id: a Celery redelivery or retry that re-inserts the
    same result is treated as success rather than an error.
    """

    INSERT = """
        INSERT INTO dbo.moderation_events (
            request_id, content_id, content_type, content_sha256,
            status, decision, decided_by,
            risk_score, predicted_label, label_scores, gate_matches,
            gate_version, model_version, thresholds, latency_ms, decided_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def emit(self, result: dict) -> None:
        conn = get_connection(self.settings)
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(self.INSERT, *self._params(result))
            except Exception as exc:
                if _is_duplicate_key(exc):
                    log.info("result %s already stored, skipping duplicate insert", result.get("request_id"))
                    return
                raise
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _params(result: dict) -> tuple:
        gate_version = result.get("gate_version")
        return (
            result.get("request_id"),
            result.get("content_id"),
            result.get("content_type"),
            result.get("content_sha256"),
            result.get("status"),
            result.get("decision"),
            result.get("decided_by"),
            result.get("risk_score"),
            result.get("predicted_label"),
            json.dumps(result.get("label_scores")),
            json.dumps(result.get("gate_matches")),
            None if gate_version is None else str(gate_version),
            result.get("model_version"),
            json.dumps(result.get("thresholds")),
            json.dumps(result.get("latency_ms")),
            result.get("decided_at"),
        )


def _is_duplicate_key(exc: Exception) -> bool:
    text = " ".join(str(a) for a in getattr(exc, "args", ()))
    return any(f"({code})" in text for code in _DUPLICATE_KEY_ERRORS)


class FailSafeSink:
    """Wraps a sink so a storage failure is logged but never fails the request.

    The full result (minus content) goes to the error log tagged
    `db_write_failed`, so it can be replayed into the database later.
    """

    def __init__(self, inner: ResultSink):
        self.inner = inner

    def emit(self, result: dict) -> None:
        try:
            self.inner.emit(result)
        except Exception:
            log.exception("db_write_failed request_id=%s result=%s", result.get("request_id"), _loggable(result))


def build_sink(settings: Settings) -> ResultSink:
    if settings.result_sink == "sql":
        return FailSafeSink(SqlServerSink(settings))
    return LoggingSink()


class MemorySink:
    """Test helper."""

    def __init__(self) -> None:
        self.results: list[dict] = []

    def emit(self, result: dict) -> None:
        self.results.append(result)
