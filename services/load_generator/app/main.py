"""Load generator: virtual shoppers, background jobs and the chaos control API."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel
from starlette.responses import Response

from common.logging import setup_logging

from .chaos import MODES, chaos
from .config import settings
from .jobs import start_jobs
from .metrics import ACTIVE_USERS, TARGET_RPS
from .shoppers_db import Catalog, shopper

log = setup_logging(settings.service_name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = AsyncConnectionPool(settings.conninfo, min_size=2, max_size=settings.db_pool_max, open=False)
    await pool.open(wait=True, timeout=60)
    catalog = Catalog()
    await catalog.load(pool)

    tasks = start_jobs(pool)
    tasks += [asyncio.create_task(shopper(i, pool, catalog), name=f"shopper-{i}")
              for i in range(settings.users)]
    ACTIVE_USERS.set(settings.users)
    TARGET_RPS.set(settings.rps)
    log.info("load generator started", extra={"users": settings.users, "rps": settings.rps})
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await pool.close()


app = FastAPI(title="load-generator", lifespan=lifespan)


class ChaosRequest(BaseModel):
    mode: str


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/chaos")
async def get_chaos() -> dict:
    return {"mode": chaos.mode, "available": MODES}


@app.post("/chaos")
async def set_chaos(req: ChaosRequest) -> dict:
    try:
        chaos.set(req.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    TARGET_RPS.set(settings.rps * chaos.rps_multiplier)
    return {"mode": chaos.mode}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.http_port, log_config=None)
