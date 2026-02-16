from __future__ import annotations

from django.conf import settings
from django.db import models

from app.api.v1.catalog.models import Product


class Reservation(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    qty = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["product", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"Reservation(user={self.user_id}, product={self.product_id}, qty={self.qty})"


class Order(models.Model):
    class Status(models.TextChoices):
        CREATED = "created", "Created"
        PAID = "paid", "Paid"
        CANCELED = "canceled", "Canceled"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CREATED)
    total_cents = models.IntegerField(default=0)
    currency = models.CharField(max_length=8, default="EUR")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"Order(id={self.id}, user={self.user_id}, status={self.status})"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    qty = models.PositiveIntegerField()
    price_cents = models.IntegerField()

    class Meta:
        indexes = [
            models.Index(fields=["order"]),
            models.Index(fields=["product"]),
        ]

    def __str__(self) -> str:
        return f"OrderItem(order={self.order_id}, product={self.product_id}, qty={self.qty})"


class IdempotencyKey(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    key = models.CharField(max_length=128)
    payload_hash = models.CharField(max_length=64)
    response_json = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        unique_together = [("user", "key")]
        indexes = [models.Index(fields=["user", "created_at"])]

    def __str__(self) -> str:
        return f"IdempotencyKey(user={self.user_id}, key={self.key})"


class OutboxEvent(models.Model):
    topic = models.CharField(max_length=128)
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["topic", "created_at"]),
            models.Index(fields=["published_at", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"OutboxEvent(topic={self.topic}, id={self.id})"
