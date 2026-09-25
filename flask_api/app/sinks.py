"""Result sinks: where finished moderation results go.

This is the single hand-off point to Intern 3's data layer. Whichever process
produces the final result (Flask in sync mode, the Celery worker in async mode)
calls `sink.emit(result)`.
"""
from __future__ import annotations

import json
import logging
from typing import Protocol

from app.db import get_connection

log = logging.getLogger("moderation.results")


class ResultSink(Protocol):
    def emit(self, result: dict) -> None:
        ...


class LoggingSink:
    """Default: one JSON line per result. Content is omitted from logs."""

    def emit(self, result: dict) -> None:
        log.info(
            json.dumps(
                {k: v for k, v in result.items() if k != "content"}
            )
        )


class SqlServerSink:
    """Writes completed moderation results to SQL Server."""

    def emit(self, result: dict) -> None:
        conn = get_connection()

        try:
            cursor = conn.cursor()

            query = """
            INSERT INTO dbo.moderation_events (
                request_id,
                content_id,
                content_type,
                content_sha256,
                status,
                decision,
                decided_by,
                risk_score,
                predicted_label,
                label_scores,
                gate_matches,
                gate_version,
                model_version,
                thresholds,
                latency_ms,
                decided_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """

            cursor.execute(
                query,
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
                result.get("gate_version"),
                result.get("model_version"),
                json.dumps(result.get("thresholds")),
                json.dumps(result.get("latency_ms")),
                result.get("decided_at"),
            )

            conn.commit()

        finally:
            conn.close()


class MemorySink:
    """Test helper."""

    def __init__(self) -> None:
        self.results: list[dict] = []

    def emit(self, result: dict) -> None:
        self.results.append(result)