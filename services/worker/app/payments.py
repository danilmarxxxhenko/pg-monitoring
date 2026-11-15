"""Payment processing for one order (idempotent)."""
from __future__ import annotations

import asyncio
import logging
import random

from opentelemetry import trace
from psycopg_pool import AsyncConnectionPool

from common.metrics import db_timer

log = logging.getLogger(__name__)
_tracer = trace.get_tracer("worker.payments")


class PaymentProcessor:
    def __init__(self, pool: AsyncConnectionPool, failure_rate: float) -> None:
        self.pool = pool
        self.base_failure_rate = failure_rate
        self.failure_rate = failure_rate

    async def process(self, order_id: int) -> str:
        """Returns the outcome: paid | failed | duplicate | missing."""
        trace.get_current_span().set_attribute("order.id", order_id)
        # 1. Register the attempt; only orders still in "created" are processed.
        async with db_timer("payment_register"), self.pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO payments (order_id, status, amount, attempts) "
                "SELECT id, 'pending', total, 1 FROM orders WHERE id = %s AND status = 'created' "
                "ON CONFLICT (order_id) DO UPDATE SET status = 'pending', "
                "attempts = payments.attempts + 1, updated_at = now() "
                "RETURNING attempts",
                (order_id,))
            row = await cur.fetchone()
        if row is None:
            return await self._classify_skipped(order_id)

        # 2. Call the (simulated) payment gateway outside of any transaction.
        with _tracer.start_as_current_span("payment.gateway",
                                           attributes={"order.id": order_id, "payment.attempt": row[0],
                                                       "payment.failure_rate": self.failure_rate}) as span:
            await asyncio.sleep(random.uniform(0.5, 3.0))
            success = random.random() >= self.failure_rate
            span.set_attribute("payment.success", success)
            if not success:
                span.set_status(trace.Status(trace.StatusCode.ERROR, "payment declined"))

        # 3. Finalise atomically; re-check the status under a row lock.
        async with db_timer("payment_finalize"), self.pool.connection() as conn, conn.transaction():
            cur = await conn.execute("SELECT status FROM orders WHERE id = %s FOR UPDATE", (order_id,))
            current = await cur.fetchone()
            if current is None:
                return "missing"
            if current[0] != "created":
                return "duplicate"
            await conn.execute(
                "UPDATE payments SET status = %s, updated_at = now() WHERE order_id = %s",
                ("succeeded" if success else "failed", order_id))
            await conn.execute(
                "UPDATE orders SET status = %s, updated_at = now(), "
                "paid_at = CASE WHEN %s THEN now() END WHERE id = %s",
                ("paid" if success else "failed", success, order_id))
            if not success:
                # Return reserved stock. Lock product rows in id order (same as order
                # creation) so this path cannot deadlock with concurrent orders.
                await conn.execute(
                    "SELECT p.id FROM products p JOIN order_items oi ON oi.product_id = p.id "
                    "WHERE oi.order_id = %s ORDER BY p.id FOR UPDATE OF p", (order_id,))
                await conn.execute(
                    "UPDATE products p SET stock = p.stock + oi.quantity, updated_at = now() "
                    "FROM order_items oi WHERE oi.order_id = %s AND oi.product_id = p.id", (order_id,))
        return "paid" if success else "failed"

    async def _classify_skipped(self, order_id: int) -> str:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT 1 FROM orders WHERE id = %s", (order_id,))
            return "duplicate" if await cur.fetchone() else "missing"
