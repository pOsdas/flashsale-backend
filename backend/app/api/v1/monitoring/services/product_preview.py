from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.api.v1.monitoring.services.product_cache import (
    ProductCacheBusyError,
    ProductCacheError,
    ProductCacheService,
)
from app.api.v1.monitoring.services.fetcher_client import (
    MonitoringFetcherError,
    MonitoringFetcherTemporarilyUnavailableError,
)
from app.core.logging import get_logger


logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ProductPreviewData:
    external_id: str
    title: str
    seller_name: str
    brand: str
    price: int | None
    old_price: int | None
    currency: str
    is_available: bool
    rating: float | None
    reviews_count: int | None
    raw_data: dict[str, Any]


class ProductPreviewError(Exception):
    pass


class ProductPreviewBusyError(ProductPreviewError):
    pass


class ProductPreviewTemporarilyUnavailableError(ProductPreviewError):
    def __init__(
            self,
            message: str,
            *,
            retry_after_seconds: int,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(1, int(retry_after_seconds))


class ProductPreviewService:
    def __init__(
            self,
            *,
            product_cache_service: ProductCacheService | None = None,
    ) -> None:
        self.product_cache_service = product_cache_service or ProductCacheService()

    def preview_product(
            self,
            *,
            marketplace: str,
            url: str,
    ) -> ProductPreviewData:
        try:
            cache_result = self.product_cache_service.get_or_refresh_product_by_identity(
                marketplace=marketplace,
                url=url,
                fallback_interval_minutes=60,
                log_identity="product_preview",
                interactive=True,
            )

        except ProductCacheBusyError as exc:
            logger.warning(
                "Product preview cache refresh is busy",
                extra={
                    "service": "product_preview",
                    "marketplace": marketplace,
                    "url": url,
                    "error": str(exc),
                },
            )
            raise ProductPreviewBusyError(
                "Товар уже обновляется. Попробуйте еще раз через несколько секунд."
            ) from exc

        except MonitoringFetcherTemporarilyUnavailableError as exc:
            retry_after_seconds = max(
                1,
                int(exc.retry_after_seconds),
            )
            logger.warning(
                "Product preview fetcher is temporarily unavailable",
                extra={
                    "service": "product_preview",
                    "marketplace": marketplace,
                    "url": url,
                    "error": str(exc),
                    "retry_after_seconds": retry_after_seconds,
                },
            )
            raise ProductPreviewTemporarilyUnavailableError(
                _build_temporary_unavailable_message(
                    error=str(exc),
                    retry_after_seconds=retry_after_seconds,
                ),
                retry_after_seconds=retry_after_seconds,
            ) from exc

        except MonitoringFetcherError as exc:
            logger.warning(
                "Product preview fetcher error",
                extra={
                    "service": "product_preview",
                    "marketplace": marketplace,
                    "url": url,
                    "error": str(exc),
                },
            )
            raise ProductPreviewError(
                "Не удалось получить товар. Проверьте ссылку или попробуйте позже."
            ) from exc

        except ProductCacheError as exc:
            logger.warning(
                "Product preview cache error",
                extra={
                    "service": "product_preview",
                    "marketplace": marketplace,
                    "url": url,
                    "error": str(exc),
                },
            )
            raise ProductPreviewError(
                "Не удалось получить товар из кеша."
            ) from exc

        except Exception as exc:
            logger.warning(
                "Product preview unexpected error",
                extra={
                    "service": "product_preview",
                    "marketplace": marketplace,
                    "url": url,
                    "error": str(exc),
                },
            )
            raise ProductPreviewError(
                "Сервис парсинга временно недоступен."
            ) from exc

        product = cache_result.product

        if not product.external_id:
            raise ProductPreviewError(
                "Товар найден, но сервис парсинга не вернул внешний идентификатор товара."
            )

        if not product.title:
            raise ProductPreviewError(
                "Товар найден, но сервис парсинга не вернул название товара."
            )

        return ProductPreviewData(
            external_id=product.external_id,
            title=product.title,
            seller_name=product.seller_name,
            brand=product.brand,
            price=self._decimal_to_int_or_none(product.price),
            old_price=self._decimal_to_int_or_none(product.old_price),
            currency=product.currency,
            is_available=bool(product.is_available),
            rating=self._decimal_to_float_or_none(product.rating),
            reviews_count=product.reviews_count,
            raw_data={
                "source": "product_cache_service",
                "cache": {
                    "source": cache_result.source,
                    "is_stale": cache_result.is_stale,
                    "parsed_at": cache_result.parsed_at.isoformat(),
                    "expires_at": cache_result.expires_at.isoformat(),
                    "effective_cache_minutes": cache_result.effective_cache_minutes,
                },
                "product": product.raw_data,
            },
        )

    def _decimal_to_int_or_none(self, value: Decimal | None) -> int | None:
        if value is None:
            return None

        return int(value)

    def _decimal_to_float_or_none(self, value: Decimal | None) -> float | None:
        if value is None:
            return None

        return float(value)


def _build_temporary_unavailable_message(
        *,
        error: str,
        retry_after_seconds: int,
) -> str:
    normalized_error = error.lower()
    retry_text = f"через ~{retry_after_seconds} сек."

    if (
            "vpn" in normalized_error
            and (
                "not ready" in normalized_error
                or "preflight" in normalized_error
                or "parse window" in normalized_error
            )
    ):
        return (
            "VPN-профиль для проверки товара сейчас подготавливается. "
            f"Повторите попытку {retry_text}"
        )

    if (
            "busy" in normalized_error
            or "status=429" in normalized_error
    ):
        return (
            "Сервис проверки товара сейчас занят. "
            f"Повторите попытку {retry_text}"
        )

    return (
        "Сервис проверки товара временно недоступен. "
        f"Повторите попытку {retry_text}"
    )
