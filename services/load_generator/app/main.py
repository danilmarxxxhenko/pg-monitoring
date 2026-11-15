"""Load generator: virtual shoppers, background jobs and the chaos control API."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from prometheus_client import Counter
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from common.logging import setup_logging
from common.metrics import metrics_response
from common.telemetry import setup_tracing

from .catalog import Catalog
from .chaos import chaos
from .config import settings
from .jobs import start_jobs
from .metrics import ACTIVE_USERS, TARGET_RPS
from .shoppers import ApiSession, Shopper

log = setup_logging(settings.service_name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = AsyncConnectionPool(settings.conninfo, min_size=2, max_size=settings.db_pool_max, open=False)
    await pool.open(wait=True, timeout=60)
    catalog = Catalog()
    await catalog.load(pool)

    client = httpx.AsyncClient(timeout=httpx.Timeout(10.0),
                               limits=httpx.Limits(max_connections=100, max_keepalive_connections=50))
    api = ApiSession(client)
    await api.login()
    await chaos.start(client, catalog.product_ids)

    tasks = start_jobs(pool)
    tasks += [asyncio.create_task(Shopper(i, api, catalog).run(), name=f"shopper-{i}")
              for i in range(settings.users)]
    ACTIVE_USERS.set(settings.users)
    TARGET_RPS.set(settings.rps)
    log.info("load generator started", extra={"users": settings.users, "rps": settings.rps,
                                              "api_url": settings.api_url})
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await chaos.close()
        await client.aclose()
        await pool.close()


app = FastAPI(title="load-generator", lifespan=lifespan)
setup_tracing(settings.service_name, fastapi_app=app, httpx_client=True, psycopg=True, aio_pika=True)


class ChaosRequest(BaseModel):
    mode: str


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/metrics")
async def metrics(request: Request):
    return metrics_response(request)


@app.get("/chaos")
async def get_chaos() -> dict:
    return chaos.status()


@app.post("/chaos")
async def set_chaos(req: ChaosRequest) -> dict:
    try:
        await chaos.set(req.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    TARGET_RPS.set(settings.rps * chaos.rps_multiplier)
    return chaos.status()


ALERTS_RECEIVED = Counter("alerts_received_total", "Grafana alert notifications received", ["alertname", "status"])


@app.post("/alerts")
async def receive_alerts(request: Request) -> dict:
    """Webhook contact point of Grafana alerting: every notification becomes a log line."""
    payload = await request.json()
    for alert in payload.get("alerts", []):
        name = alert.get("labels", {}).get("alertname", "unknown")
        status = alert.get("status", payload.get("status", "unknown"))
        ALERTS_RECEIVED.labels(name, status).inc()
        log.warning("ALERT %s: %s", status.upper(), name, extra={
            "event": "alert", "alertname": name, "alert_status": status,
            "severity": alert.get("labels", {}).get("severity", ""),
            "summary": alert.get("annotations", {}).get("summary", ""),
            "started_at": alert.get("startsAt", ""),
        })
    return {"received": len(payload.get("alerts", []))}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.http_port, log_config=None)
