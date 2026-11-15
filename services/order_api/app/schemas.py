"""Request/response models."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

OrderStatus = Literal["created", "paid", "failed", "archived"]


class Product(BaseModel):
    id: int
    name: str
    category: str
    price: Decimal
    stock: int
    updated_at: datetime


class OrderItemIn(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=100)


class OrderIn(BaseModel):
    customer_id: int = Field(gt=0)
    items: list[OrderItemIn] = Field(min_length=1, max_length=20)


class OrderItem(BaseModel):
    product_id: int
    quantity: int
    unit_price: Decimal


class Payment(BaseModel):
    status: str
    amount: Decimal
    attempts: int
    updated_at: datetime


class Order(BaseModel):
    id: int
    customer_id: int
    status: OrderStatus
    total: Decimal
    created_at: datetime
    updated_at: datetime
    paid_at: datetime | None = None
    items: list[OrderItem] = []
    payment: Payment | None = None


class OrderSummary(BaseModel):
    id: int
    customer_id: int
    status: OrderStatus
    total: Decimal
    created_at: datetime
