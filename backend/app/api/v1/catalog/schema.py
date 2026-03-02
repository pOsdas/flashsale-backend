from typing import List, Optional
import strawberry
from strawberry import auto
from strawberry.types import Info
from strawberry_django import type as dj_type

from app.api.v1.catalog.models import Stock, Product


# ---- Types ----
@dj_type(Product)
class ProductType:
    id: auto
    sku: auto
    title: auto
    price_cents: auto
    currency: auto
    is_active: auto

    stock: Optional["StockType"]


@dj_type(Stock)
class StockType:
    id: auto
    product: auto
    available: auto


# ---- Query ----
@strawberry.type
class CatalogQuery:
    @strawberry.field
    def product(self, info: Info, sku: str) -> Optional[ProductType]:
        # avoid N+1
        return (
            Product.objects.select_related("stock")
            .filter(sku=sku)
            .first()
        )

    @strawberry.field
    def products(
            self,
            info: Info,
            is_active: Optional[bool] = None,
            limit: int = 50,
            offset: int = 0,
    ) -> List[ProductType]:
        qs = Product.objects.select_related("stock").all().order_by("id")

        if is_active is not None:
            qs = qs.filter(is_active=is_active)

        return list(qs[offset: offset + limit])


# ---- Inputs ----
@strawberry.input
class ProductCreateInput:
    sku: str
    title: str
    price_cents: int
    currency: str = "EUR"
    is_active: bool = True
    available: int = 0


@strawberry.input
class StockSetInput:
    sku: str
    available: int


# ---- Mutations ----
@strawberry.type
class CatalogMutation:
    @strawberry.mutation
    def create_product(self, info: Info, data: ProductCreateInput) -> ProductType:
        product = Product.objects.create(
            sku=data.sku,
            title=data.title,
            price_cents=data.price_cents,
            currency=data.currency,
            is_active=data.is_active,
        )
        Stock.objects.create(product=product, available=data.available)
        return Product.objects.select_related("stock").get(pk=product.pk)

    @strawberry.mutation
    def get_stock(self, info: Info, data: StockSetInput) -> ProductType:
        product = Product.objects.get(sku=data.sku)
        stock, _ = Stock.objects.get_or_create(product=product)
        stock.available = data.available
        stock.save(update_fields=["available"])
        return stock
