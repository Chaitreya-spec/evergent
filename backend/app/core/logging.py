"""Structured logging.

Every log line carries the ids needed to trace a request end to end:
request -> workflow -> task -> agent -> model -> tool.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog

from app.core.config import settings

# Bound per request/workflow so log lines are correlated without threading ids
# through every function signature.
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
workflow_id_ctx: ContextVar[str | None] = ContextVar("workflow_id", default=None)
task_id_ctx: ContextVar[str | None] = ContextVar("task_id", default=None)


def _inject_context(
    _logger: Any, _name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    for key, ctx in (
        ("request_id", request_id_ctx),
        ("workflow_id", workflow_id_ctx),
        ("task_id", task_id_ctx),
    ):
        value = ctx.get()
        if value is not None:
            event_dict.setdefault(key, value)
    return event_dict


def configure_logging() -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.DEBUG if settings.debug else logging.INFO,
    )

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _inject_context,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # Human-readable locally, JSON in production where logs are ingested.
    processors.append(
        structlog.processors.JSONRenderer()
        if settings.is_production
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if settings.debug else logging.INFO
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    return structlog.get_logger(name)
