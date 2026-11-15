"""Reference data the virtual shoppers pick from (ids of products and customers)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from psycopg_pool import AsyncConnectionPool

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
