"""Virtual shoppers working directly against PostgreSQL.

Stage-1 mode: there is no order API yet, so shoppers issue the same SQL the
service will issue later, and payment is simulated in-process.
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field

from psycopg_pool import AsyncConnectionPool

from .chaos import chaos
from .config import settings
from .metrics import timed

log = logging.getLogger(__name__)

SEARCH_TERMS = ("smart", "pro", "laptop", "coffee", "chair", "ball", "jacket", "puzzle",
                "cream", "tea", "lamp", "novel", "mini", "eco", "retro", "bike")


@dataclass
class Catalog:
    product_ids: list[int] = field(default_factory=list)
    customer_ids: list[int] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)

    async def load(self, pool: AsyncConnectionPool) -> None:
        async with pool.connection() as conn:
            self.product_ids = [r[0] for r in await (await conn.execute("SELECT id FROM products")).fetchall()]
            self.customer_ids = [r[0] for r in await (await conn.execute("SELECT id FROM customers")).fetchall()]
            self.categories = [r[0] for r in await (await conn.execute(
                "SELECT DISTINCT category FROM products ORDER BY 1")).fetchall()]
        log.info("catalog loaded", extra={"products": len(self.product_ids),
                                          "customers": len(self.customer_ids)})


class OutOfStock(Exception):
    pass


async def browse_category(pool: AsyncConnectionPool, cat: Catalog) -> None:
    async with timed("browse_category"), pool.connection() as conn:
        await conn.execute(
            "SELECT id, name, price, stock FROM products WHERE category = %s "
            "ORDER BY price LIMIT %s",
            (random.choice(cat.categories), random.choice((10, 20, 50))),
        )


async def search(pool: AsyncConnectionPool, cat: Catalog) -> None:
    async with timed("search_products"), pool.connection() as conn:
        await conn.execute(
            "SELECT id, name, category, price FROM products WHERE name ILIKE %s "
            "ORDER BY name LIMIT 20",
            (f"%{random.choice(SEARCH_TERMS)}%",),
        )


async def view_product(pool: AsyncConnectionPool, cat: Catalog) -> None:
    async with timed("get_product"), pool.connection() as conn:
        await conn.execute(
            "SELECT id, name, category, price, stock, updated_at FROM products WHERE id = %s",
            (random.choice(cat.product_ids),),
        )


async def order_history(pool: AsyncConnectionPool, cat: Catalog) -> None:
    async with timed("list_orders"), pool.connection() as conn:
        await conn.execute(
            "SELECT o.id, o.status, o.total, o.created_at, count(oi.id) AS items "
            "FROM orders o LEFT JOIN order_items oi ON oi.order_id = o.id "
            "WHERE o.customer_id = %s GROUP BY o.id ORDER BY o.created_at DESC LIMIT 10",
            (random.choice(cat.customer_ids),),
        )


async def create_order(pool: AsyncConnectionPool, cat: Catalog) -> None:
    wanted = {pid: random.randint(1, 3) for pid in random.sample(cat.product_ids, random.randint(1, 4))}
    customer_id = random.choice(cat.customer_ids)
    async with timed("create_order") as outcome:
        try:
            async with pool.connection() as conn, conn.transaction():
                # Lock rows in a stable order to avoid deadlocks between shoppers.
                rows = await (await conn.execute(
                    "SELECT id, price, stock FROM products WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
                    (list(wanted),),
                )).fetchall()
                if any(stock < wanted[pid] for pid, _, stock in rows):
                    raise OutOfStock()
                total = sum(price * wanted[pid] for pid, price, _ in rows)
                order_id = (await (await conn.execute(
                    "INSERT INTO orders (customer_id, total) VALUES (%s, %s) RETURNING id",
                    (customer_id, total),
                )).fetchone())[0]
                async with conn.cursor() as cur:
                    await cur.executemany(
                        "INSERT INTO order_items (order_id, product_id, quantity, unit_price) "
                        "VALUES (%s, %s, %s, %s)",
                        [(order_id, pid, wanted[pid], price) for pid, price, _ in rows],
                    )
                    await cur.executemany(
                        "UPDATE products SET stock = stock - %s, updated_at = now() WHERE id = %s",
                        [(wanted[pid], pid) for pid, _, _ in rows],
                    )
        except OutOfStock:
            outcome.result = "out_of_stock"
            return
    task = asyncio.create_task(_process_payment(pool, order_id, total, wanted))
    _payment_tasks.add(task)
    task.add_done_callback(_payment_tasks.discard)


_payment_tasks: set[asyncio.Task] = set()


async def _process_payment(pool: AsyncConnectionPool, order_id: int, amount, items: dict[int, int]) -> None:
    """In-process stand-in for the payment worker (replaced at stage 2)."""
    await asyncio.sleep(random.uniform(0.5, 3.0))
    success = random.random() >= 0.1
    async with timed("process_payment") as outcome, pool.connection() as conn, conn.transaction():
        outcome.result = "paid" if success else "failed"
        await conn.execute(
            "INSERT INTO payments (order_id, status, amount, attempts) VALUES (%s, %s, %s, 1)",
            (order_id, "succeeded" if success else "failed", amount),
        )
        await conn.execute(
            "UPDATE orders SET status = %s, updated_at = now(), "
            "paid_at = CASE WHEN %s THEN now() END WHERE id = %s",
            ("paid" if success else "failed", success, order_id),
        )
        if not success:
            async with conn.cursor() as cur:
                await cur.executemany(
                    "UPDATE products SET stock = stock + %s, updated_at = now() WHERE id = %s",
                    [(qty, pid) for pid, qty in sorted(items.items())],
                )


ACTIONS = (
    (browse_category, 40),
    (search, 25),
    (view_product, 20),
    (create_order, 10),
    (order_history, 5),
)


async def shopper(n: int, pool: AsyncConnectionPool, cat: Catalog) -> None:
    funcs, weights = zip(*ACTIONS)
    while True:
        per_user_rate = settings.rps * chaos.rps_multiplier / settings.users
        await asyncio.sleep(random.expovariate(per_user_rate))
        action = random.choices(funcs, weights)[0]
        try:
            await action(pool, cat)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("shopper action failed", extra={"shopper": n, "action": action.__name__})
