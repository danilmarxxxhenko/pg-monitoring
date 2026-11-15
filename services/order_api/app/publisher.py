"""Publishing of order events to RabbitMQ."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal

import aio_pika
from aio_pika.abc import AbstractExchange, AbstractRobustConnection
from prometheus_client import Counter

from common import mq

log = logging.getLogger(__name__)

PUBLISHED = Counter("mq_messages_published_total", "Messages published to RabbitMQ", ["routing_key", "result"])


class Publisher:
    def __init__(self) -> None:
        self.connection: AbstractRobustConnection | None = None
        self.exchange: AbstractExchange | None = None

    async def start(self, service: str) -> None:
        self.connection = await mq.connect(service)
        channel = await self.connection.channel(publisher_confirms=True)
        self.exchange, _ = await mq.declare_orders(channel)

    async def close(self) -> None:
        if self.connection:
            await self.connection.close()

    @property
    def connected(self) -> bool:
        return bool(self.connection and not self.connection.is_closed)

    async def order_created(self, order: dict) -> bool:
        """Publish `order.created`; returns False if the broker did not confirm."""
        body = {
            "order_id": order["id"],
            "customer_id": order["customer_id"],
            "total": str(order["total"]),
            "items": [{"product_id": i["product_id"], "quantity": i["quantity"]} for i in order.get("items", [])],
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
        message = aio_pika.Message(
            json.dumps(body, default=_json_default).encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=f"order-{order['id']}",
            type=mq.ORDER_CREATED_KEY,
        )
        try:
            assert self.exchange is not None
            await self.exchange.publish(message, routing_key=mq.ORDER_CREATED_KEY, timeout=5)
        except Exception as exc:
            PUBLISHED.labels(mq.ORDER_CREATED_KEY, "error").inc()
            log.error("publish failed, order will be re-published later",
                      extra={"order_id": order["id"], "error": repr(exc)})
            return False
        PUBLISHED.labels(mq.ORDER_CREATED_KEY, "ok").inc()
        return True


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value))
