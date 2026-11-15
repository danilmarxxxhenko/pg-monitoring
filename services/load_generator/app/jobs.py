"""Periodic background jobs that keep the data "alive".

Together with shoppers they produce every kind of SQL statement:
SELECT, INSERT, UPDATE and DELETE.
"""
from __future__ import annotations

import asyncio
import logging
import random

from opentelemetry import trace
from psycopg_pool import AsyncConnectionPool

from .config import settings
from .metrics import timed

log = logging.getLogger(__name__)
_tracer = trace.get_tracer("load-generator.jobs")


async def restock(pool: AsyncConnectionPool) -> None:
    async with timed("job_restock"), pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE products SET stock = stock + 50 + floor(random() * 150)::int, updated_at = now() "
            "WHERE stock < 20"
        )
        log.info("restocked products", extra={"rows": cur.rowcount})


async def reprice(pool: AsyncConnectionPool) -> None:
    async with timed("job_reprice"), pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE products SET price = greatest(1, round((price * (0.9 + random() * 0.2))::numeric, 2)), "
            "updated_at = now() "
            "WHERE id IN (SELECT id FROM products ORDER BY random() LIMIT %s)",
            (random.randint(10, 40),),
        )
        log.info("repriced products", extra={"rows": cur.rowcount})


async def archive(pool: AsyncConnectionPool) -> None:
    async with timed("job_archive"), pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE orders SET status = 'archived', updated_at = now() "
            "WHERE status IN ('paid', 'failed') AND created_at < now() - make_interval(mins => %s)",
            (settings.archive_after_min,),
        )
        log.info("archived orders", extra={"rows": cur.rowcount})


async def purge(pool: AsyncConnectionPool) -> None:
    async with timed("job_purge"), pool.connection() as conn:
        # Batched retention purge; order_items and payments go via ON DELETE CASCADE
        cur = await conn.execute(
            "DELETE FROM orders WHERE id IN ("
            "  SELECT id FROM orders WHERE status = 'archived' "
            "  AND created_at < now() - make_interval(mins => %s) ORDER BY id LIMIT %s)",
            (settings.purge_after_min, settings.purge_batch),
        )
        log.info("purged archived orders", extra={"rows": cur.rowcount})


async def _every(period: float, job, pool: AsyncConnectionPool) -> None:
    await asyncio.sleep(random.uniform(1, period))  # spread the first runs
    while True:
        try:
            with _tracer.start_as_current_span(f"job.{job.__name__}"):
                await job(pool)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("background job failed", extra={"job": job.__name__})
        await asyncio.sleep(period)


def start_jobs(pool: AsyncConnectionPool) -> list[asyncio.Task]:
    schedule = (
        (settings.restock_every, restock),
        (settings.reprice_every, reprice),
        (settings.archive_every, archive),
        (settings.purge_every, purge),
    )
    return [asyncio.create_task(_every(p, job, pool), name=job.__name__) for p, job in schedule]
