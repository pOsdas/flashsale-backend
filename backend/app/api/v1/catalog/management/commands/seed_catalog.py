import random
import string
from typing import List

from django.core.management.base import BaseCommand
from django.db import transaction

from app.api.v1.catalog.models import Product, Stock


def _rand_suffix(n: int = 6) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(n))


def _make_sku(i: int) -> str:
    return f"FS-{i:04d}-{_rand_suffix(6)}"


class Command(BaseCommand):
    help = "Seeds catalog with products and stocks"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--count",
            type=int,
            default=150,
            help="How many products to create (default: 150).",
        )
        parser.add_argument(
            "--truncate",
            action="store_true",
            help="Delete all Product/Stock before seeding.",
        )
        parser.add_argument(
            "--min-price",
            type=int,
            default=199,
            help="Min price in cents (default: 199).",
        )
        parser.add_argument(
            "--max-price",
            type=int,
            default=19999,
            help="Max price in cents (default: 19999).",
        )
        parser.add_argument(
            "--max-stock",
            type=int,
            default=50,
            help="Max available stock per product (default: 50).",
        )

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        count: int = options["count"]
        truncate: bool = options["truncate"]
        min_price: int = options["min_price"]
        max_price: int = options["max_price"]
        max_stock: int = options["max_stock"]

        if count <= 0:
            self.stdout.write(self.style.WARNING("Nothing to do: --count must be > 0"))
            return

        if min_price < 0 or max_price < 0 or min_price > max_price:
            raise ValueError("Invalid price range. Ensure 0 <= min-price <= max-price.")

        if max_stock < 0:
            raise ValueError("Invalid --max-stock, must be >= 0.")

        if truncate:
            deleted_products, _ = Product.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Truncated catalog, deleted products: {deleted_products}"))

        existing_skus = set(Product.objects.values_list("sku", flat=True))

        products_to_create: List[Product] = []
        target_total = count

        i = 1
        while len(products_to_create) < target_total:
            sku = _make_sku(i)
            i += 1
            if sku in existing_skus:
                continue

            price_cents = random.randint(min_price, max_price)
            currency = "EUR"
            is_active = True

            title = f"FlashSale Product #{len(products_to_create) + 1}"

            products_to_create.append(
                Product(
                    sku=sku,
                    title=title,
                    price_cents=price_cents,
                    currency=currency,
                    is_active=is_active,
                )
            )
            existing_skus.add(sku)

        Product.objects.bulk_create(products_to_create, batch_size=500)

        created_skus = [p.sku for p in products_to_create]
        created_products = list(Product.objects.filter(sku__in=created_skus).only("id", "sku"))

        stocks_to_create: List[Stock] = []
        for p in created_products:
            available = random.randint(0, max_stock)
            stocks_to_create.append(Stock(product_id=p.id, available=available))

        Stock.objects.bulk_create(stocks_to_create, batch_size=500)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded: products={len(created_products)}, stocks={len(stocks_to_create)}"
            )
        )