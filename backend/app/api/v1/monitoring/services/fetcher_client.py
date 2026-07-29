import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

import httpx
from django.conf import settings

from app.api.v1.monitoring.fetcher_metrics import (
    MONITORING_GO_FETCHER_LAST_SUCCESS_TIMESTAMP_SECONDS,
    MONITORING_GO_FETCHER_REQUEST_DURATION_SECONDS,
    MONITORING_GO_FETCHER_REQUESTS_IN_PROGRESS,
    MONITORING_GO_FETCHER_REQUESTS_TOTAL,
)
from app.api.v1.monitoring.metric_utils import (
    build_http_status_class,
    normalize_marketplace_label,
)
from app.api.v1.monitoring.models import Marketplace, MonitoringTarget
from app.core.logging import get_logger


logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class FetchedProductData:
    external_id: str
    title: str
    seller_name: str
    brand: str
    price: Decimal | None
    old_price: Decimal | None
    currency: str
    is_available: bool | None
    rating: Decimal | None
    reviews_count: int | None
    raw_data: dict[str, Any]


class MonitoringFetcherError(Exception):
    pass


class MonitoringFetcherApplicationError(MonitoringFetcherError):
    pass


class MonitoringFetcherInvalidResponseError(MonitoringFetcherError):
    pass


class MonitoringFetcherTemporarilyUnavailableError(MonitoringFetcherError):
    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: int = 300,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(1, int(retry_after_seconds))


class MonitoringFetcherClient:
    def fetch_target(self, *, target: MonitoringTarget) -> FetchedProductData:
        return self.fetch_product(
            marketplace=target.marketplace,
            url=target.url,
            external_id=target.external_id,
            title=target.title,
            seller_name=target.seller_name,
            brand=target.brand,
            log_identity=str(target.id),
        )

    def fetch_product(
        self,
        *,
        marketplace: str,
        url: str,
        external_id: str = "",
        title: str = "",
        seller_name: str = "",
        brand: str = "",
        log_identity: str = "",
    ) -> FetchedProductData:
        raise NotImplementedError


class HttpMonitoringFetcherClient(MonitoringFetcherClient):
    def __init__(
        self,
        *,
        base_url: str,
        product_endpoint: str,
        api_key: str,
        timeout_seconds: int,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.product_endpoint = product_endpoint.lstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def fetch_product(
        self,
        *,
        marketplace: str,
        url: str,
        external_id: str = "",
        title: str = "",
        seller_name: str = "",
        brand: str = "",
        log_identity: str = "",
    ) -> FetchedProductData:
        endpoint = urljoin(self.base_url, self.product_endpoint)
        marketplace_label = normalize_marketplace_label(marketplace)
        started_at = time.monotonic()
        result = "error"
        status_class = "none"

        payload = {
            "marketplace": marketplace,
            "url": url,
            "external_id": external_id,
        }

        headers = {
            "Content-Type": "application/json",
        }

        if self.api_key:
            headers["X-Fetcher-Api-Key"] = self.api_key

        logger.warning(
            "GO_FETCHER_REQUEST_URL",
            extra={
                "service": "monitoring",
                "log_identity": log_identity,
                "marketplace": marketplace,
                "base_url": self.base_url,
                "product_endpoint": self.product_endpoint,
                "full_url": endpoint,
                "timeout_seconds": self.timeout_seconds,
                "has_api_key": bool(self.api_key),
            },
        )

        MONITORING_GO_FETCHER_REQUESTS_IN_PROGRESS.labels(
            marketplace=marketplace_label,
        ).inc()

        try:
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(
                        endpoint,
                        json=payload,
                        headers=headers,
                    )

                status_class = build_http_status_class(
                    response.status_code
                )
                response.raise_for_status()

            except httpx.TimeoutException as exc:
                result = "timeout"
                raise MonitoringFetcherError(
                    f"go_fetcher request timeout: {exc}"
                ) from exc

            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                status_class = build_http_status_class(status_code)
                response_text = exc.response.text[:1000]

                if status_code in {425, 429, 503}:
                    result = "temporarily_unavailable"
                    retry_after_seconds = _parse_retry_after_seconds(
                        exc.response.headers.get("Retry-After")
                    )
                    error_message = _extract_fetcher_error_message(
                        response=exc.response,
                        fallback=response_text,
                    )
                    raise MonitoringFetcherTemporarilyUnavailableError(
                        "go_fetcher is temporarily unavailable: "
                        f"{error_message}",
                        retry_after_seconds=retry_after_seconds,
                    ) from exc

                result = "http_error"
                raise MonitoringFetcherError(
                    "go_fetcher returned HTTP "
                    f"{status_code}: {response_text}"
                ) from exc

            except httpx.HTTPError as exc:
                result = "network_error"

                logger.exception(
                    "GO_FETCHER_REQUEST_FAILED",
                    extra={
                        "service": "monitoring",
                        "log_identity": log_identity,
                        "marketplace": marketplace,
                        "full_url": endpoint,
                        "error": str(exc),
                    },
                )

                raise MonitoringFetcherError(
                    f"go_fetcher request failed: {exc}"
                ) from exc

            try:
                data = response.json()
            except ValueError as exc:
                result = "invalid_json"
                raise MonitoringFetcherInvalidResponseError(
                    "go_fetcher returned invalid JSON"
                ) from exc

            try:
                product = self._parse_response(
                    data=data,
                    fallback_external_id=external_id,
                    fallback_title=title,
                    fallback_seller_name=seller_name,
                    fallback_brand=brand,
                )
            except MonitoringFetcherApplicationError:
                result = "application_error"
                raise
            except MonitoringFetcherInvalidResponseError:
                result = "invalid_response"
                raise

            result = "success"
            MONITORING_GO_FETCHER_LAST_SUCCESS_TIMESTAMP_SECONDS.labels(
                marketplace=marketplace_label,
            ).set(time.time())

            return product

        finally:
            MONITORING_GO_FETCHER_REQUESTS_TOTAL.labels(
                marketplace=marketplace_label,
                result=result,
                status_class=status_class,
            ).inc()

            MONITORING_GO_FETCHER_REQUEST_DURATION_SECONDS.labels(
                marketplace=marketplace_label,
                result=result,
            ).observe(
                time.monotonic() - started_at
            )

            MONITORING_GO_FETCHER_REQUESTS_IN_PROGRESS.labels(
                marketplace=marketplace_label,
            ).dec()

    def _parse_response(
        self,
        *,
        data: dict[str, Any],
        fallback_external_id: str = "",
        fallback_title: str = "",
        fallback_seller_name: str = "",
        fallback_brand: str = "",
    ) -> FetchedProductData:
        if not isinstance(data, dict):
            raise MonitoringFetcherInvalidResponseError(
                "go_fetcher response must be JSON object"
            )

        if data.get("status") == "error":
            error_message = (
                data.get("error")
                or data.get("message")
                or "unknown fetcher error"
            )
            raise MonitoringFetcherApplicationError(
                str(error_message)
            )

        product_data = data.get("product", data)

        if not isinstance(product_data, dict):
            raise MonitoringFetcherInvalidResponseError(
                "go_fetcher product payload must be JSON object"
            )

        external_id = str(
            product_data.get("external_id")
            or product_data.get("sku")
            or product_data.get("id")
            or fallback_external_id
            or ""
        )

        title = str(
            product_data.get("title")
            or product_data.get("name")
            or fallback_title
            or ""
        )

        seller_name = str(
            product_data.get("seller_name")
            or product_data.get("seller")
            or fallback_seller_name
            or ""
        )

        brand = str(
            product_data.get("brand")
            or fallback_brand
            or ""
        )

        price = _cents_to_decimal_or_none(
            product_data.get("price_cents")
        )
        old_price = _cents_to_decimal_or_none(
            product_data.get("old_price_cents")
        )

        currency = str(
            product_data.get("currency")
            or "RUB"
        )

        is_available = _to_bool_or_none(
            product_data.get("is_available")
            if "is_available" in product_data
            else product_data.get("available")
        )

        rating = _to_decimal_or_none(
            product_data.get("rating")
        )

        reviews_count = _to_int_or_none(
            product_data.get("reviews_count")
            or product_data.get("review_count")
            or product_data.get("feedbacks")
        )

        if not external_id:
            raise MonitoringFetcherInvalidResponseError(
                "go_fetcher response does not contain "
                "external_id/sku/id"
            )

        return FetchedProductData(
            external_id=external_id,
            title=title,
            seller_name=seller_name,
            brand=brand,
            price=price,
            old_price=old_price,
            currency=currency,
            is_available=is_available,
            rating=rating,
            reviews_count=reviews_count,
            raw_data={
                "source": "go_fetcher",
                "response": data,
            },
        )


class FakeMonitoringFetcherClient(MonitoringFetcherClient):
    """
    Temporary fake fetcher for local MVP pipeline testing.
    Use MONITORING_FETCHER_MODE=fake to enable it.
    """

    def fetch_target(self, *, target: MonitoringTarget) -> FetchedProductData:
        snapshots_count = target.snapshots.count()

        base_price = Decimal("1000.00")

        if snapshots_count % 3 == 0:
            price = base_price
            rating = Decimal("4.80")
            reviews_count = 100
            is_available = True
        elif snapshots_count % 3 == 1:
            price = Decimal("920.00")
            rating = Decimal("4.70")
            reviews_count = 112
            is_available = True
        else:
            price = Decimal("870.00")
            rating = Decimal("4.65")
            reviews_count = 128
            is_available = False

        external_id = (
            target.external_id
            or self._extract_demo_external_id(
                marketplace=target.marketplace
            )
        )

        return FetchedProductData(
            external_id=external_id,
            title=(
                target.title
                or self._build_demo_title(
                    marketplace=target.marketplace
                )
            ),
            seller_name=target.seller_name or "Demo Seller",
            brand=target.brand or "Demo Brand",
            price=price,
            old_price=Decimal("1200.00"),
            currency="RUB",
            is_available=is_available,
            rating=rating,
            reviews_count=reviews_count,
            raw_data={
                "source": "fake_monitoring_fetcher",
                "marketplace": target.marketplace,
                "url": target.url,
                "snapshots_count_before_fetch": snapshots_count,
            },
        )

    def fetch_product(
        self,
        *,
        marketplace: str,
        url: str,
        external_id: str = "",
        title: str = "",
        seller_name: str = "",
        brand: str = "",
        log_identity: str = "",
    ) -> FetchedProductData:
        resolved_external_id = (
            external_id
            or self._extract_demo_external_id(
                marketplace=marketplace
            )
        )

        return FetchedProductData(
            external_id=resolved_external_id,
            title=(
                title
                or self._build_demo_title(
                    marketplace=marketplace
                )
            ),
            seller_name=seller_name or "Demo Seller",
            brand=brand or "Demo Brand",
            price=Decimal("1000.00"),
            old_price=Decimal("1200.00"),
            currency="RUB",
            is_available=True,
            rating=Decimal("4.80"),
            reviews_count=100,
            raw_data={
                "source": "fake_monitoring_fetcher",
                "marketplace": marketplace,
                "url": url,
                "log_identity": log_identity,
            },
        )

    def _extract_demo_external_id(self, *, marketplace: str) -> str:
        if marketplace == Marketplace.WILDBERRIES:
            return "fake-wb-product"

        if marketplace == Marketplace.OZON:
            return "fake-ozon-product"

        return "fake-product"

    def _build_demo_title(self, *, marketplace: str) -> str:
        if marketplace == Marketplace.WILDBERRIES:
            return "Demo Wildberries Product"

        if marketplace == Marketplace.OZON:
            return "Demo Ozon Product"

        return "Demo Marketplace Product"


def build_monitoring_fetcher_client() -> MonitoringFetcherClient:
    mode = settings.MONITORING_FETCHER_MODE.lower().strip()

    if mode == "fake":
        return FakeMonitoringFetcherClient()

    if mode == "http":
        return HttpMonitoringFetcherClient(
            base_url=settings.GO_FETCHER_BASE_URL,
            product_endpoint=settings.GO_FETCHER_PRODUCT_ENDPOINT,
            api_key=settings.GO_FETCHER_API_KEY,
            timeout_seconds=settings.GO_FETCHER_TIMEOUT_SECONDS,
        )

    raise RuntimeError(
        "Unsupported "
        f"MONITORING_FETCHER_MODE={settings.MONITORING_FETCHER_MODE!r}. "
        "Allowed values: fake, http."
    )


def _to_decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None

    if value == "":
        return None

    try:
        return Decimal(str(value))
    except Exception as exc:
        raise MonitoringFetcherInvalidResponseError(
            f"Invalid decimal value: {value!r}"
        ) from exc



def _parse_retry_after_seconds(raw_value: str | None) -> int:
    try:
        value = int(str(raw_value or "").strip())
    except (TypeError, ValueError):
        return 300
    return max(1, min(value, 3600))


def _extract_fetcher_error_message(
    *,
    response: httpx.Response,
    fallback: str,
) -> str:
    try:
        payload = response.json()
    except ValueError:
        return fallback or "temporary gateway unavailability"

    if isinstance(payload, dict):
        message = payload.get("error") or payload.get("message")
        if message:
            return str(message)[:1000]

    return fallback or "temporary gateway unavailability"


def _cents_to_decimal_or_none(value: Any) -> Decimal | None:
    cents = _to_int_or_none(value)
    if cents is None:
        return None

    return Decimal(cents) / Decimal("100")


def _to_int_or_none(value: Any) -> int | None:
    if value is None:
        return None

    if value == "":
        return None

    try:
        return int(value)
    except Exception as exc:
        raise MonitoringFetcherInvalidResponseError(
            f"Invalid integer value: {value!r}"
        ) from exc


def _to_bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.lower().strip()

        if normalized in {
            "true",
            "1",
            "yes",
            "available",
            "in_stock",
        }:
            return True

        if normalized in {
            "false",
            "0",
            "no",
            "unavailable",
            "out_of_stock",
        }:
            return False

    if isinstance(value, int):
        return bool(value)

    raise MonitoringFetcherInvalidResponseError(
        f"Invalid boolean value: {value!r}"
    )
