"""Prometheus metrics shared by all HTTP services.

* `http_request_duration_seconds{method, route, status}` with trace_id exemplars
* `db_query_duration_seconds{operation}` for database operations
* `/metrics` endpoint with OpenMetrics negotiation (exemplars are exposed
  only in the OpenMetrics format)
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from prometheus_client import REGISTRY, Gauge, Histogram
from prometheus_client import exposition as text_exposition
from prometheus_client.openmetrics import exposition as om_exposition
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

try:
    from opentelemetry import trace as _otel_trace
except ImportError:  # pragma: no cover
    _otel_trace = None

log = logging.getLogger("http")

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 1, 1.5, 2.5, 5, 10)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency by route template",
    ["method", "route", "status"],
    buckets=LATENCY_BUCKETS,
)
HTTP_IN_PROGRESS = Gauge("http_requests_in_progress", "HTTP requests being served", ["method"])
DB_QUERY_DURATION = Histogram(
    "db_query_duration_seconds",
    "Duration of database operations issued by the service",
    ["operation"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

_SILENT_PATHS = {"/metrics", "/health"}


def trace_exemplar() -> dict[str, str] | None:
    """Exemplar labels pointing at the current trace, if any."""
    if _otel_trace is None:
        return None
    ctx = _otel_trace.get_current_span().get_span_context()
    if not ctx.is_valid or not ctx.trace_flags.sampled:
        return None
    return {"trace_id": format(ctx.trace_id, "032x")}


@asynccontextmanager
async def db_timer(operation: str):
    start = time.perf_counter()
    try:
        yield
    finally:
        DB_QUERY_DURATION.labels(operation).observe(time.perf_counter() - start, exemplar=trace_exemplar())


class HttpMetricsMiddleware:
    """Pure ASGI middleware: latency histogram + one JSON access-log line per request.

    It must run *inside* the OpenTelemetry middleware so that the server span
    is current when exemplars and log fields are produced.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"]
        status = 500
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        HTTP_IN_PROGRESS.labels(method).inc()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            HTTP_IN_PROGRESS.labels(method).dec()
            elapsed = time.perf_counter() - start
            route = scope.get("route")
            # Route template keeps label cardinality bounded (/orders/{order_id})
            route_path = getattr(route, "path", None) or "unmatched"
            HTTP_REQUEST_DURATION.labels(method, route_path, str(status)).observe(
                elapsed, exemplar=trace_exemplar())
            path = scope.get("path", "")
            if path not in _SILENT_PATHS:
                level = logging.ERROR if status >= 500 else logging.WARNING if status >= 400 else logging.INFO
                log.log(level, "%s %s -> %s", method, path, status, extra={
                    "http_method": method, "http_route": route_path, "http_path": path,
                    "http_status": status, "duration_ms": round(elapsed * 1000, 2),
                    "client_ip": _client_ip(scope),
                })


def _client_ip(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name == b"x-real-ip":
            return value.decode()
    client = scope.get("client")
    return client[0] if client else ""


def metrics_response(request: Request) -> Response:
    """Serve metrics, preferring OpenMetrics (required for exemplars)."""
    accept = request.headers.get("accept", "")
    if "application/openmetrics-text" in accept:
        return Response(om_exposition.generate_latest(REGISTRY),
                        media_type=om_exposition.CONTENT_TYPE_LATEST)
    return Response(text_exposition.generate_latest(REGISTRY),
                    media_type=text_exposition.CONTENT_TYPE_LATEST)
