"""Virtual shoppers that use the order API over HTTP."""
from __future__ import annotations

import asyncio
import logging
import os
import random
from collections import deque

import httpx

from .catalog import SEARCH_TERMS, Catalog
from .chaos import chaos
from .config import settings
from .metrics import REQUEST_DURATION, timed

log = logging.getLogger(__name__)


class ApiSession:
    """HTTP client of the order API holding the service account's JWT.

    Login is skipped while no service credentials are configured (stage 2);
    a 401 response triggers a fresh login.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.token: str | None = None
        self._lock = asyncio.Lock()
        self.user = os.getenv("SERVICE_USER", "")
        self.password = os.getenv("SERVICE_PASSWORD", "")

    async def login(self) -> None:
        if not (self.user and self.password):
            return
        async with self._lock:
            resp = await self.client.post(settings.token_url,
                                          json={"username": self.user, "password": self.password})
            resp.raise_for_status()
            self.token = resp.json()["access_token"]
            log.info("service account logged in", extra={"user": self.user})

    async def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
            resp = await self.client.request(method, settings.api_url + path, headers=headers, **kwargs)
            if resp.status_code == 401 and attempt == 1 and self.user:
                await self.login()
                continue
            return resp
        return resp


async def _call(api: ApiSession, operation: str, method: str, path: str,
                ok: tuple[int, ...] = (200,), **kwargs) -> httpx.Response | None:
    async with timed(operation, REQUEST_DURATION) as outcome:
        resp = await api.request(method, path, **kwargs)
        outcome.result = "ok" if resp.status_code in ok else str(resp.status_code)
        return resp


class Shopper:
    def __init__(self, n: int, api: ApiSession, catalog: Catalog) -> None:
        self.n, self.api, self.catalog = n, api, catalog
        self.customer_id = random.choice(catalog.customer_ids)
        self.seen_products: deque[int] = deque(maxlen=30)
        self.my_orders: deque[int] = deque(maxlen=10)

    # ------------------------------------------------------------ actions
    async def browse_category(self) -> None:
        resp = await _call(self.api, "browse_category", "GET", "/products",
                           params={"category": random.choice(self.catalog.categories),
                                   "limit": random.choice((10, 20, 50))})
        self._remember(resp)

    async def search(self) -> None:
        resp = await _call(self.api, "search_products", "GET", "/products",
                           params={"search": random.choice(SEARCH_TERMS), "limit": 20})
        self._remember(resp)

    async def view_product(self) -> None:
        pid = random.choice(self.seen_products or self.catalog.product_ids)
        await _call(self.api, "get_product", "GET", f"/products/{pid}")

    async def create_order(self) -> None:
        pool = list(self.seen_products) if len(self.seen_products) >= 4 else self.catalog.product_ids
        items = [{"product_id": pid, "quantity": random.randint(1, 3)}
                 for pid in random.sample(pool, random.randint(1, min(4, len(pool))))]
        resp = await _call(self.api, "create_order", "POST", "/orders", ok=(201, 409),
                           json={"customer_id": self.customer_id, "items": items})
        if resp is not None and resp.status_code == 201:
            self.my_orders.append(resp.json()["id"])

    async def check_order(self) -> None:
        if not self.my_orders:
            return await self.list_orders()
        await _call(self.api, "get_order", "GET", f"/orders/{random.choice(self.my_orders)}")

    async def list_orders(self) -> None:
        params = {"customer_id": self.customer_id, "limit": 10}
        if random.random() < 0.3:
            params["status"] = random.choice(("paid", "failed", "created"))
        await _call(self.api, "list_orders", "GET", "/orders", params=params)

    def _remember(self, resp: httpx.Response | None) -> None:
        if resp is not None and resp.status_code == 200:
            self.seen_products.extend(p["id"] for p in resp.json())

    # -------------------------------------------------------------- loop
    async def run(self) -> None:
        actions = (
            (self.browse_category, 35),
            (self.search, 25),
            (self.view_product, 20),
            (self.create_order, 10),
            (self.check_order, 6),
            (self.list_orders, 4),
        )
        funcs, weights = zip(*actions)
        while True:
            per_user_rate = settings.rps * chaos.rps_multiplier / settings.users
            await asyncio.sleep(random.expovariate(per_user_rate))
            action = random.choices(funcs, weights)[0]
            try:
                await action()
            except asyncio.CancelledError:
                raise
            except httpx.HTTPError as exc:
                log.warning("api call failed", extra={"shopper": self.n, "action": action.__name__,
                                                      "error": repr(exc)})
            except Exception:
                log.exception("shopper action failed", extra={"shopper": self.n, "action": action.__name__})
