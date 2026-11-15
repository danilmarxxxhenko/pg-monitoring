"""Chaos scenarios that make performance problems visible on the dashboards.

Modes:
    off               normal load
    slow_queries      order-api wraps catalog queries with pg_sleep()     (via RabbitMQ `control`)
    lock_contention   concurrent long transactions UPDATE the same "hot" product rows in
                      random order (lock waits, idle-in-transaction sessions, deadlocks);
                      shoppers order those products too
    traffic_spike     x10 request rate
    brute_force       a stream of failed logins with guessed usernames/passwords
    payment_failures  worker declines ~70% of payments                      (via RabbitMQ `control`)

The current mode is broadcast through the `control` fanout exchange on every
change and every 15 s, so services that restart converge to it.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import random
import secrets
import time

import aio_pika
import httpx
import psycopg
from opentelemetry import trace
from psycopg_pool import AsyncConnectionPool

from common import mq

from .config import settings
from .metrics import CHAOS_MODE, OPERATIONS

log = logging.getLogger(__name__)
_tracer = trace.get_tracer("load-generator.chaos")

MODES = (
    "off",
    "slow_queries",
    "lock_contention",
    "traffic_spike",
    "brute_force",
    "payment_failures",
)
GUESSED_USERS = ("admin", "root", "administrator", "postgres", "test", "user", "guest", "operator")


def _background(coro, name: str) -> asyncio.Task:
    """Start a long-lived task with an empty context.

    asyncio copies contextvars into new tasks; a task started from the
    POST /chaos handler would otherwise inherit that request's span, and every
    iteration would end up in one endless trace.
    """
    return asyncio.create_task(coro, name=name, context=contextvars.Context())


class ChaosController:
    def __init__(self) -> None:
        self.mode = "off"
        self.since = time.time()
        self.hot_products: list[int] = []
        self._tasks: list[asyncio.Task] = []
        self._lock_pool: AsyncConnectionPool | None = None
        self._exchange: aio_pika.abc.AbstractExchange | None = None
        self._connection = None
        self._http: httpx.AsyncClient | None = None
        self._heartbeat: asyncio.Task | None = None
        self._publish_gauge()

    # ---------------------------------------------------------- lifecycle
    async def start(self, http: httpx.AsyncClient, product_ids: list[int]) -> None:
        self._http = http
        self.hot_products = sorted(random.sample(product_ids, settings.chaos_hot_products))
        self._connection = await mq.connect(settings.service_name)
        channel = await self._connection.channel()
        self._exchange = await mq.declare_control(channel)
        self._heartbeat = _background(self._broadcast_loop(), "chaos-heartbeat")

    async def close(self) -> None:
        await self._stop_mode_tasks()
        if self._heartbeat:
            self._heartbeat.cancel()
        if self._connection:
            await self._connection.close()

    # -------------------------------------------------------------- state
    @property
    def rps_multiplier(self) -> float:
        return 10.0 if self.mode == "traffic_spike" else 1.0

    async def set(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown chaos mode {mode!r}, expected one of {MODES}")
        previous = self.mode
        await self._stop_mode_tasks()
        self.mode, self.since = mode, time.time()
        if mode == "lock_contention":
            await self._start_lock_contention()
        elif mode == "brute_force":
            self._tasks.append(_background(self._brute_force(), "chaos-brute-force"))
        self._publish_gauge()
        await self._broadcast()
        log.warning("chaos mode changed", extra={"chaos_from": previous, "chaos_to": mode})

    def status(self) -> dict:
        return {"mode": self.mode, "since": round(self.since), "available": MODES,
                "hot_products": self.hot_products}

    def _publish_gauge(self) -> None:
        for m in MODES:
            CHAOS_MODE.labels(m).set(1 if m == self.mode else 0)

    async def _broadcast(self) -> None:
        if self._exchange is None:
            return
        body = json.dumps({"mode": self.mode, "since": self.since}).encode()
        await self._exchange.publish(aio_pika.Message(body, content_type="application/json"), routing_key="")

    async def _broadcast_loop(self) -> None:
        while True:
            await asyncio.sleep(15)
            try:
                await self._broadcast()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("chaos broadcast failed")

    async def _stop_mode_tasks(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._lock_pool is not None:
            await self._lock_pool.close()
            self._lock_pool = None

    # ---------------------------------------------------- lock contention
    async def _start_lock_contention(self) -> None:
        n = settings.chaos_lock_sessions
        self._lock_pool = AsyncConnectionPool(
            settings.conninfo.replace(f"application_name={settings.service_name}",
                                      f"application_name={settings.service_name}-chaos"),
            min_size=n, max_size=n, open=False)
        await self._lock_pool.open(wait=True, timeout=30)
        self._tasks += [_background(self._lock_session(i), f"chaos-lock-{i}") for i in range(n)]

    async def _lock_session(self, n: int) -> None:
        assert self._lock_pool is not None
        while True:
            # Random order on purpose: two sessions locking (a, b) and (b, a) deadlock
            first, second = random.sample(self.hot_products, 2)
            with _tracer.start_as_current_span("chaos.lock_contention",
                                               attributes={"products": [first, second]}) as span:
                try:
                    async with self._lock_pool.connection() as conn, conn.transaction():
                        await conn.execute(
                            "UPDATE products SET stock = stock, updated_at = now() WHERE id = %s", (first,))
                        await asyncio.sleep(random.uniform(0.3, 1.0))   # "idle in transaction" holding the lock
                        await conn.execute(
                            "UPDATE products SET stock = stock, updated_at = now() WHERE id = %s", (second,))
                        await asyncio.sleep(random.uniform(0.2, 0.6))
                    OPERATIONS.labels("chaos_lock", "ok").inc()
                except psycopg.errors.DeadlockDetected:
                    OPERATIONS.labels("chaos_lock", "deadlock").inc()
                    span.set_status(trace.Status(trace.StatusCode.ERROR, "deadlock detected"))
                    log.warning("deadlock detected", extra={"session": n, "products": [first, second]})
                except asyncio.CancelledError:
                    raise
                except Exception:
                    OPERATIONS.labels("chaos_lock", "error").inc()
                    log.exception("lock contention session failed", extra={"session": n})
                    await asyncio.sleep(1)

    # -------------------------------------------------------- brute force
    async def _brute_force(self) -> None:
        assert self._http is not None
        interval = 1.0 / settings.chaos_bruteforce_rps
        while True:
            username = random.choice(GUESSED_USERS)
            with _tracer.start_as_current_span("chaos.brute_force", attributes={"username": username}):
                try:
                    resp = await self._http.post(settings.token_url, json={
                        "username": username, "password": secrets.token_urlsafe(8)})
                    OPERATIONS.labels("chaos_login", str(resp.status_code)).inc()
                except httpx.HTTPError as exc:
                    OPERATIONS.labels("chaos_login", "error").inc()
                    log.warning("brute force request failed", extra={"error": repr(exc)})
            await asyncio.sleep(random.expovariate(1.0 / interval))


chaos = ChaosController()
