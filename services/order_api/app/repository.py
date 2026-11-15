"""SQL access layer of the order API."""
from __future__ import annotations

import random
from decimal import Decimal

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from common.metrics import db_timer

from .schemas import OrderIn


class NotFound(Exception):
    pass


class OutOfStock(Exception):
    def __init__(self, product_ids: list[int]) -> None:
        super().__init__(f"insufficient stock for products {product_ids}")
        self.product_ids = product_ids


class Repository:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool
        # Chaos "slow_queries": catalog statements get a pg_sleep() inside PostgreSQL
        self.slow_queries: bool = False

    # ---------------------------------------------------------- catalog
    async def list_products(self, search: str | None, category: str | None, limit: int) -> list[dict]:
        where, params = [], []
        if search:
            where.append("name ILIKE %s")
            params.append(f"%{search}%")
        if category:
            where.append("category = %s")
            params.append(category)
        sql = "SELECT id, name, category, price, stock, updated_at FROM products"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY " + ("name" if search else "price") + " LIMIT %s"
        params.append(limit)
        if self.slow_queries:
            # The statement itself becomes slow in PostgreSQL (visible in pg_stat_statements)
            sql = f"SELECT p.* FROM ({sql}) AS p, pg_sleep(%s)"
            params.append(round(random.uniform(0.3, 1.2), 2))
        async with db_timer("list_products"), self.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(sql, params)
            return await cur.fetchall()

    async def get_product(self, product_id: int) -> dict:
        async with db_timer("get_product"), self.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(
                "SELECT id, name, category, price, stock, updated_at FROM products WHERE id = %s",
                (product_id,))
            row = await cur.fetchone()
        if row is None:
            raise NotFound()
        return row

    # ----------------------------------------------------------- orders
    async def create_order(self, order: OrderIn) -> dict:
        wanted: dict[int, int] = {}
        for item in order.items:  # merge duplicate lines of the same product
            wanted[item.product_id] = wanted.get(item.product_id, 0) + item.quantity

        async with db_timer("create_order"), self.pool.connection() as conn, conn.transaction():
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute("SELECT id FROM customers WHERE id = %s", (order.customer_id,))
            if await cur.fetchone() is None:
                raise NotFound()
            # Lock product rows in id order: concurrent orders cannot deadlock
            await cur.execute(
                "SELECT id, price, stock FROM products WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
                (list(wanted),))
            rows = await cur.fetchall()
            if len(rows) != len(wanted):
                raise NotFound()
            short = [r["id"] for r in rows if r["stock"] < wanted[r["id"]]]
            if short:
                raise OutOfStock(short)

            total = sum((r["price"] * wanted[r["id"]] for r in rows), Decimal("0"))
            await cur.execute(
                "INSERT INTO orders (customer_id, total) VALUES (%s, %s) "
                "RETURNING id, customer_id, status, total, created_at, updated_at, paid_at",
                (order.customer_id, total))
            created = await cur.fetchone()
            await cur.executemany(
                "INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (%s, %s, %s, %s)",
                [(created["id"], r["id"], wanted[r["id"]], r["price"]) for r in rows])
            await cur.executemany(
                "UPDATE products SET stock = stock - %s, updated_at = now() WHERE id = %s",
                [(wanted[r["id"]], r["id"]) for r in rows])
        created["items"] = [{"product_id": r["id"], "quantity": wanted[r["id"]], "unit_price": r["price"]}
                            for r in rows]
        return created

    async def get_order(self, order_id: int) -> dict:
        async with db_timer("get_order"), self.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(
                "SELECT id, customer_id, status, total, created_at, updated_at, paid_at "
                "FROM orders WHERE id = %s", (order_id,))
            order = await cur.fetchone()
            if order is None:
                raise NotFound()
            await cur.execute(
                "SELECT product_id, quantity, unit_price FROM order_items WHERE order_id = %s ORDER BY id",
                (order_id,))
            order["items"] = await cur.fetchall()
            await cur.execute(
                "SELECT status, amount, attempts, updated_at FROM payments WHERE order_id = %s", (order_id,))
            order["payment"] = await cur.fetchone()
        return order

    async def list_orders(self, status: str | None, customer_id: int | None, limit: int) -> list[dict]:
        where, params = [], []
        if status:
            where.append("status = %s")
            params.append(status)
        if customer_id:
            where.append("customer_id = %s")
            params.append(customer_id)
        sql = "SELECT id, customer_id, status, total, created_at FROM orders"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)
        async with db_timer("list_orders"), self.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(sql, params)
            return await cur.fetchall()

    async def stale_created_orders(self, older_than_s: int, limit: int = 100) -> list[dict]:
        async with db_timer("stale_orders"), self.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(
                "SELECT id, customer_id, total FROM orders WHERE status = 'created' "
                "AND created_at < now() - make_interval(secs => %s) ORDER BY id LIMIT %s",
                (older_than_s, limit))
            return await cur.fetchall()

    async def ping(self) -> None:
        async with self.pool.connection(timeout=2) as conn:
            await conn.execute("SELECT 1")
