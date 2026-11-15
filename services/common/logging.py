"""Structured JSON logging to stdout.

Every record carries `level`, `service`, `trace_id` and `span_id` so that Loki
can correlate log lines with Tempo traces.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

try:
    from opentelemetry import trace as _otel_trace
except ImportError:  # pragma: no cover - OTel is optional for tooling scripts
    _otel_trace = None

# Attributes present on every LogRecord; everything else came via `extra=`.
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName", "color_message"}


def _current_trace_ids() -> tuple[str, str]:
    if _otel_trace is None:
        return "", ""
    ctx = _otel_trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return "", ""
    return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        trace_id, span_id = _current_trace_ids()
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": trace_id,
            "span_id": span_id,
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(service: str) -> logging.Logger:
    """Configure the root logger (and uvicorn's) to emit JSON to stdout."""
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    # Access logs are produced by our own HTTP middleware with route/status/duration.
    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("httpx", "httpcore", "aiormq", "aio_pika", "psycopg.pool"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return logging.getLogger(service)
