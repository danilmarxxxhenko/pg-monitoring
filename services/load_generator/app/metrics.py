"""Prometheus metrics of the load generator."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from prometheus_client import Counter, Gauge, Histogram

from common.metrics import DB_QUERY_DURATION, LATENCY_BUCKETS, trace_exemplar

OPERATIONS = Counter(
    "loadgen_operations_total",
    "Operations executed by virtual shoppers and background jobs",
    ["operation", "result"],
)
REQUEST_DURATION = Histogram(
    "loadgen_request_duration_seconds",
    "Client-side latency of API calls made by virtual shoppers",
    ["operation"],
    buckets=LATENCY_BUCKETS,
)
ACTIVE_USERS = Gauge("loadgen_active_users", "Virtual shoppers currently running")
TARGET_RPS = Gauge("loadgen_target_rps", "Current target request rate")
CHAOS_MODE = Gauge("loadgen_chaos_mode", "Active chaos mode (1 = active)", ["mode"])


class Outcome:
    """Mutable outcome label; the caller may override the default "ok"."""

    __slots__ = ("result",)

    def __init__(self) -> None:
        self.result = "ok"


@asynccontextmanager
async def timed(operation: str, histogram: Histogram = DB_QUERY_DURATION):
    """Measure an operation and count its outcome."""
    outcome = Outcome()
    start = time.perf_counter()
    try:
        yield outcome
    except Exception:
        outcome.result = "error"
        raise
    finally:
        histogram.labels(operation).observe(time.perf_counter() - start, exemplar=trace_exemplar())
        OPERATIONS.labels(operation, outcome.result).inc()
