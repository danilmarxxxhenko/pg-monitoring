"""Order API: catalog browsing and order placement for the online shop."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from prometheus_client import Counter
from psycopg_pool import AsyncConnectionPool
from starlette.responses import JSONResponse

from common.logging import setup_logging
from common.metrics import HttpMetricsMiddleware, metrics_response
from common.telemetry import setup_tracing

from .auth import Principal, require_user
from .config import settings
from .publisher import Publisher
from .repository import NotFound, OutOfStock, Repository
from .schemas import Order, OrderIn, OrderStatus, OrderSummary, Product

log = setup_logging(settings.service_name)

ORDERS_CREATED = Counter("orders_created_total", "Orders successfully created")
ORDERS_REJECTED = Counter("orders_rejected_total", "Orders rejected", ["reason"])
ORDERS_REPUBLISHED = Counter("orders_republished_total", "Stale orders re-published to RabbitMQ")


async def _republish_stale(app: FastAPI) -> None:
    """Outbox-like safety net: orders left in "created" are published again.

    The worker is idempotent, so a duplicate message is harmless.
    """
    while True:
        await asyncio.sleep(settings.republish_every_s)
        try:
            stale = await app.state.repo.stale_created_orders(settings.republish_after_s)
            for order in stale:
                if await app.state.publisher.order_created(order):
                    ORDERS_REPUBLISHED.inc()
            if stale:
                log.warning("re-published stale orders", extra={"count": len(stale)})
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("stale order re-publish failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = AsyncConnectionPool(settings.conninfo, min_size=settings.db_pool_min,
                               max_size=settings.db_pool_max, open=False, timeout=10)
    await pool.open(wait=True, timeout=60)
    app.state.repo = Repository(pool)
    app.state.publisher = Publisher()
    await app.state.publisher.start(settings.service_name)
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(3.0))
    task = asyncio.create_task(_republish_stale(app))
    log.info("order api started", extra={"auth_enabled": settings.auth_enabled})
    try:
        yield
    finally:
        task.cancel()
        await app.state.http.aclose()
        await app.state.publisher.close()
        await pool.close()


app = FastAPI(title="order-api", version="1.0.0", lifespan=lifespan)
app.add_middleware(HttpMetricsMiddleware)
setup_tracing(settings.service_name, fastapi_app=app, httpx_client=True, psycopg=True, aio_pika=True)

User = Annotated[Principal, Depends(require_user)]


def repo(request: Request) -> Repository:
    return request.app.state.repo


# ------------------------------------------------------------------ service
@app.get("/health", include_in_schema=False)
async def health(request: Request):
    problems = []
    try:
        await repo(request).ping()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"db: {exc!r}")
    if not request.app.state.publisher.connected:
        problems.append("rabbitmq: disconnected")
    if problems:
        return JSONResponse({"status": "unhealthy", "problems": problems}, status_code=503)
    return {"status": "ok"}


@app.get("/metrics", include_in_schema=False)
async def metrics(request: Request):
    return metrics_response(request)


# ------------------------------------------------------------------ catalog
@app.get("/products", response_model=list[Product])
async def list_products(request: Request, user: User,
                        search: Annotated[str | None, Query(max_length=64)] = None,
                        category: Annotated[str | None, Query(max_length=32)] = None,
                        limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return await repo(request).list_products(search, category, limit)


@app.get("/products/{product_id}", response_model=Product)
async def get_product(request: Request, user: User, product_id: int):
    try:
        return await repo(request).get_product(product_id)
    except NotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product not found") from None


# ------------------------------------------------------------------- orders
@app.post("/orders", response_model=Order, status_code=status.HTTP_201_CREATED)
async def create_order(request: Request, user: User, body: OrderIn):
    try:
        order = await repo(request).create_order(body)
    except NotFound:
        ORDERS_REJECTED.labels("not_found").inc()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "customer or product not found") from None
    except OutOfStock as exc:
        ORDERS_REJECTED.labels("out_of_stock").inc()
        log.info("order rejected: out of stock", extra={"product_ids": exc.product_ids})
        raise HTTPException(status.HTTP_409_CONFLICT,
                            {"error": "out_of_stock", "product_ids": exc.product_ids}) from None
    ORDERS_CREATED.inc()
    # Published after COMMIT, so the worker never sees an uncommitted order
    await request.app.state.publisher.order_created(order)
    log.info("order created", extra={"order_id": order["id"], "customer_id": order["customer_id"],
                                     "total": str(order["total"]), "user": user.username})
    return order


@app.get("/orders/{order_id}", response_model=Order)
async def get_order(request: Request, user: User, order_id: int):
    try:
        return await repo(request).get_order(order_id)
    except NotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found") from None


@app.get("/orders", response_model=list[OrderSummary])
async def list_orders(request: Request, user: User,
                      status_: Annotated[OrderStatus | None, Query(alias="status")] = None,
                      customer_id: Annotated[int | None, Query(gt=0)] = None,
                      limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return await repo(request).list_orders(status_, customer_id, limit)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.http_port, log_config=None, proxy_headers=True,
                forwarded_allow_ips="*")
