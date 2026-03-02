from typing import List, Optional
from enum import Enum
import strawberry
from strawberry import auto
from strawberry.types import Info
from strawberry_django import type as dj_type
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from app.api.v1.catalog.models import Product
from app.api.v1.orders.models import (
    Reservation,
    Order,
    OrderItem,
    IdempotencyKey,
    OutboxEvent,
)

User = get_user_model()


# ---- Enums ----
@strawberry.enum
class OrderStatusEnum(str, Enum):
    CREATED = "created"
    PAID = "paid"
    CANCELED = "canceled"


# ---- Types ----
@dj_type(Reservation)
class ReservationType:
    id: auto
    user: auto
    product: auto
    qty: auto
    created_at: auto


@dj_type(OrderItem)
class OrderItemType:
    id: auto
    order: auto
    product: auto
    qty: auto
    price_cents: auto


@dj_type(Order)
class OrderType:
    id: auto
    user: auto
    status: auto
    total_cents: auto
    currency: auto
    created_at: auto

    items: List[OrderItemType]


@dj_type(IdempotencyKey)
class IdempotencyKeyType:
    id: auto
    user: auto
    key: auto
    payload_hash: auto
    response_json: auto
    created_at: auto


@dj_type(OutboxEvent)
class OutboxEventType:
    id: auto
    topic: auto
    payload: auto
    created_at: auto
    published_at: auto


# ---- Query ----
@strawberry.type
class OrdersQuery:

    @strawberry.field
    def my_orders(self, info: Info) -> List["OrderType"]:
        user = info.context.request.user
        return list(
            Order.objects
            .filter(user=user)
            .select_related("user")
            .prefetch_related("items__product")
            .order_by("-created_at")
        )

    @strawberry.field
    def order(self, info: Info, order_id: int) -> Optional["OrderType"]:
        user = info.context.request.user
        return (
            Order.objects
            .filter(id=order_id, user=user)
            .select_related("user")
            .prefetch_related("items__product")
            .first()
        )

    @strawberry.field
    def reservation(self, info: Info) -> List[ReservationType]:
        user = info.context.request.user
        return list(
            Reservation.objects
            .filter(user=user)
            .select_related("product")
            .order_by("-created_at")
        )


# ---- Inputs ----
@strawberry.input
class OrderItemInput:
    product_id: int
    qty: int


@strawberry.input
class CreateOrderInput:
    items: List[OrderItemInput]
    currency: str = "EUR"


# ---- Mutations ----
@strawberry.type
class OrdersMutation:

    @strawberry.mutation
    @transaction.atomic
    def create_order(self, info: Info, data: CreateOrderInput) -> OrderType:
        user = info.context.request.user

        order = Order.objects.create(
            user=user,
            status=Order.Status.CREATED,
            currency=data.currency,
        )

        total = 0

        for item in data.items:
            product = Product.objects.select_for_update().get(id=item.product_id)

            OrderItem.objects.create(
                order=order,
                product=product,
                qty=item.qty,
                price_cents=product.price_cents
            )

            total += product.price_cents * item.qty

        order.total_cents = total
        order.save(update_fields=["total_cents"])

        OutboxEvent.objects.create(
            topic="order.created",
            payload={
                "order_id": order.id,
                "user_id": user.id,
                "total_cents": order.total_cents,
            }
        )

        return (
            Order.objects
            .select_related("user")
            .prefetch_related("items__product")
            .get(id=order.id)
        )

    @strawberry.mutation
    @transaction.atomic
    def set_order_status(
            self,
            info: Info,
            order_id: int,
            status: OrderStatusEnum,
    ) -> OrderType:
        order = Order.objects.select_for_update().get(id=order_id)
        order.status = status.value
        order.save(update_fields=["status"])

        OutboxEvent.objects.create(
            topic="order.status_changed",
            payload={
                "order_id": order.id,
                "status": order.status,
            }
        )

        return order
