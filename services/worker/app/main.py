"""Payment worker: consumes `order.created` events from RabbitMQ.

* manual ack after the DB transaction commits
* one redelivery for transient errors, then the message is dead-lettered
* chaos mode broadcasts are received through the `control` fanout exchange
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager

import uvicorn
from aio_pika.abc import AbstractIncomingMessage
from fastapi import FastAPI, Request
from prometheus_client import Counter, Gauge, Histogram
from psycopg_pool import AsyncConnectionPool
from starlette.responses import JSONResponse

from common import mq
from common.logging import setup_logging
from common.metrics import metrics_response, trace_exemplar

from .payments import PaymentProcessor

SERVICE = os.getenv("SERVICE_NAME", "worker")
log = setup_logging(SERVICE)

PREFETCH = int(os.getenv("WORKER_PREFETCH", "10"))
FAILURE_RATE = float(os.getenv("PAYMENT_FAILURE_RATE", "0.1"))
CHAOS_FAILURE_RATE = float(os.getenv("CHAOS_PAYMENT_FAILURE_RATE", "0.7"))
CONNINFO = (f"host={os.getenv('DB_HOST', 'postgres')} dbname={os.getenv('DB_NAME', 'shop')} "
            f"user={os.getenv('DB_USER', 'app')} password={os.getenv('DB_PASSWORD', '')} "
            f"application_name={SERVICE}")

PROCESSED = Counter("orders_processed_total", "Order events handled by the worker", ["status"])
DURATION = Histogram("payment_processing_duration_seconds", "End-to-end handling time of one order event",
                     buckets=(0.25, 0.5, 1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10, 20))
IN_FLIGHT = Gauge("worker_messages_in_flight", "Messages currently being processed")
MQ_CONNECTED = Gauge("worker_rabbitmq_connected", "1 if the consumer is connected to RabbitMQ")
FAILURE_RATE_G = Gauge("worker_payment_failure_rate", "Configured probability of a payment failure")


class Consumer:
    def __init__(self, processor: PaymentProcessor) -> None:
        self.processor = processor
        self.connection = None

    async def start(self) -> None:
        self.connection = await mq.connect(SERVICE)
        channel = await self.connection.channel()
        await channel.set_qos(prefetch_count=PREFETCH)
        _, queue = await mq.declare_orders(channel)
        await queue.consume(self.on_order_created)

        control = await mq.declare_control(channel)
        control_queue = await channel.declare_queue(exclusive=True, auto_delete=True)
        await control_queue.bind(control)
        await control_queue.consume(self.on_control, no_ack=True)
        MQ_CONNECTED.set(1)
        self.connection.close_callbacks.add(lambda *_: MQ_CONNECTED.set(0))
        self.connection.reconnect_callbacks.add(lambda *_: MQ_CONNECTED.set(1))
        log.info("consuming", extra={"queue": mq.PAYMENTS_QUEUE, "prefetch": PREFETCH})

    async def close(self) -> None:
        if self.connection:
            await self.connection.close()

    async def on_order_created(self, message: AbstractIncomingMessage) -> None:
        start = time.perf_counter()
        IN_FLIGHT.inc()
        try:
            try:
                order_id = int(json.loads(message.body)["order_id"])
            except (ValueError, KeyError, TypeError):
                PROCESSED.labels("invalid").inc()
                log.error("invalid message dead-lettered", extra={"message_id": message.message_id})
                await message.reject(requeue=False)
                return
            try:
                outcome = await self.processor.process(order_id)
            except Exception:
                if not message.redelivered:
                    PROCESSED.labels("retried").inc()
                    log.exception("processing failed, message requeued once", extra={"order_id": order_id})
                    await message.nack(requeue=True)
                else:
                    PROCESSED.labels("dead_lettered").inc()
                    log.exception("processing failed again, message dead-lettered", extra={"order_id": order_id})
                    await message.reject(requeue=False)
                return
            await message.ack()
            PROCESSED.labels(outcome).inc()
            level = logging.WARNING if outcome == "failed" else logging.INFO
            log.log(level, "order %s: %s", order_id, outcome, extra={"order_id": order_id, "outcome": outcome})
        finally:
            IN_FLIGHT.dec()
            DURATION.observe(time.perf_counter() - start, exemplar=trace_exemplar())

    async def on_control(self, message: AbstractIncomingMessage) -> None:
        try:
            mode = json.loads(message.body).get("mode", "off")
        except ValueError:
            return
        rate = CHAOS_FAILURE_RATE if mode == "payment_failures" else FAILURE_RATE
        self.processor.failure_rate = rate
        FAILURE_RATE_G.set(rate)
        log.warning("chaos mode received", extra={"chaos_mode": mode, "failure_rate": rate})


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = AsyncConnectionPool(CONNINFO, min_size=2, max_size=int(os.getenv("DB_POOL_MAX", "10")),
                               open=False, timeout=10)
    await pool.open(wait=True, timeout=60)
    FAILURE_RATE_G.set(FAILURE_RATE)
    consumer = Consumer(PaymentProcessor(pool, FAILURE_RATE))
    await consumer.start()
    app.state.consumer = consumer
    try:
        yield
    finally:
        await consumer.close()
        await pool.close()


app = FastAPI(title="worker", lifespan=lifespan)


@app.get("/health")
async def health(request: Request):
    conn = request.app.state.consumer.connection
    if conn is None or conn.is_closed:
        return JSONResponse({"status": "unhealthy"}, status_code=503)
    return {"status": "ok"}


@app.get("/metrics")
async def metrics(request: Request):
    return metrics_response(request)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("HTTP_PORT", "8000")), log_config=None)
