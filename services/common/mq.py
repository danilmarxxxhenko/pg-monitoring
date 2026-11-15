"""RabbitMQ topology shared by the publisher (order-api) and consumers.

    orders (topic) --order.created--> payments.order-created
                                           | reject / failure
                                           v
    orders.dlx (fanout) -------------> payments.order-created.dlq

    control (fanout) --> exclusive per-instance queues (chaos mode broadcast)

Declarations are idempotent, so every service declares what it uses.
"""
from __future__ import annotations

import os

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractQueue

ORDERS_EXCHANGE = "orders"
DLX_EXCHANGE = "orders.dlx"
CONTROL_EXCHANGE = "control"
ORDER_CREATED_KEY = "order.created"
PAYMENTS_QUEUE = "payments.order-created"
PAYMENTS_DLQ = "payments.order-created.dlq"


def amqp_url() -> str:
    user = os.environ["RABBITMQ_USER"]
    password = os.environ["RABBITMQ_PASSWORD"]
    host = os.getenv("RABBITMQ_HOST", "rabbitmq")
    return f"amqp://{user}:{password}@{host}:5672/"


async def connect(service: str) -> aio_pika.abc.AbstractRobustConnection:
    """Robust connection: reconnects and re-declares topology automatically."""
    return await aio_pika.connect_robust(
        amqp_url(), client_properties={"connection_name": service}, timeout=30)


async def declare_orders(channel: AbstractChannel) -> tuple[AbstractExchange, AbstractQueue]:
    orders = await channel.declare_exchange(ORDERS_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
    dlx = await channel.declare_exchange(DLX_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True)
    dlq = await channel.declare_queue(PAYMENTS_DLQ, durable=True)
    await dlq.bind(dlx)
    queue = await channel.declare_queue(
        PAYMENTS_QUEUE, durable=True, arguments={"x-dead-letter-exchange": DLX_EXCHANGE})
    await queue.bind(orders, routing_key=ORDER_CREATED_KEY)
    return orders, queue


async def declare_control(channel: AbstractChannel) -> AbstractExchange:
    return await channel.declare_exchange(CONTROL_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True)
