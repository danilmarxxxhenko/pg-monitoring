"""Prometheus metrics of the load generator."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from prometheus_client import Counter, Gauge, Histogram

OPERATIONS = Counter(
    "loadgen_operations_total",
    "Operations executed by virtual shoppers and background jobs",
    ["operation", "result"],
)
DB_QUERY_DURATION = Histogram(
    "db_query_duration_seconds",
    "Duration of database operations issued by the service",
    ["operation"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
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
async def timed(operation: str):
    """Measure an operation and count its outcome."""
    outcome = Outcome()
    start = time.perf_counter()
    try:
        yield outcome
    except Exception:
        outcome.result = "error"
        raise
    finally:
        DB_QUERY_DURATION.labels(operation).observe(time.perf_counter() - start)
        OPERATIONS.labels(operation, outcome.result).inc()
