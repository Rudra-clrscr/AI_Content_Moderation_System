"""Result sinks: where finished moderation results go.

This is the single hand-off point to Intern 3's data layer. Whichever process
produces the final result (Flask in sync mode, the Celery worker in async mode)
calls `sink.emit(result)`. Intern 3 can plug a SQL Server writer in here, or
swap it for "enqueue a logging task" — the Flask code doesn't change.
"""
from __future__ import annotations

import json
import logging
from typing import Protocol

log = logging.getLogger("moderation.results")


class ResultSink(Protocol):
    def emit(self, result: dict) -> None: ...


class LoggingSink:
    """Default: one JSON line per result. Content is omitted from logs."""

    def emit(self, result: dict) -> None:
        log.info(json.dumps({k: v for k, v in result.items() if k != "content"}))


class MemorySink:
    """Test helper."""

    def __init__(self) -> None:
        self.results: list[dict] = []

    def emit(self, result: dict) -> None:
        self.results.append(result)
