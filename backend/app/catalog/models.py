from django.db import models


class Product(models.Model):
    sku = models.CharField(max_length=64, unique=True)
    title = models.CharField(max_length=255)
    price_cents = models.IntegerField()
    currency = models.CharField(max_length=8, default="EUR")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.sku} - {self.title}"


class Stock(models.Model):
    product = models.OneToOneField(Product, on_delete=models.CASCADE, related_name="stock")
    available = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.product.sku} - {self.available}"

