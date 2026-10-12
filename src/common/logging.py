"""Minimal JSON structured logger shared by every pipeline stage.

All records carry an optional ``run_id`` so a single pipeline execution can be
correlated across ingestion, ELT, DQ and reporting logs.
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

_run_id: ContextVar[str | None] = ContextVar("run_id", default=None)


def set_run_id(run_id: str | None) -> None:
    """Bind a run identifier to the current context."""
    _run_id.set(run_id)


def get_run_id() -> str | None:
    """Return the run identifier bound to the current context."""
    return _run_id.get()


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        run_id = get_run_id()
        if run_id:
            payload["run_id"] = run_id
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_logger(name: str = "fleet") -> logging.Logger:
    """Return a logger configured with the JSON formatter exactly once."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def log_event(logger: logging.Logger, message: str, **fields: object) -> None:
    """Emit a structured log line with additional key/value fields."""
    logger.info(message, extra={"extra_fields": fields})
