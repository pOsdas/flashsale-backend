import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from django.db import transaction
from django.db.models import Min
from django.utils import timezone

from app.api.v1.common.locks import RedisLock, RedisLockAlreadyAcquiredError
from app.api.v1.monitoring.cache_metrics import (
    MONITORING_PRODUCT_CACHE_ENTRY_AGE_SECONDS,
    MONITORING_PRODUCT_CACHE_LOCK_EVENTS_TOTAL,
    MONITORING_PRODUCT_CACHE_LOOKUPS_TOTAL,
    MONITORING_PRODUCT_CACHE_REFRESH_DURATION_SECONDS,
    MONITORING_PRODUCT_CACHE_REFRESHES_IN_PROGRESS,
    MONITORING_PRODUCT_CACHE_REFRESHES_TOTAL,
    MONITORING_PRODUCT_CACHE_REQUESTS_TOTAL,
    MONITORING_PRODUCT_CACHE_WAIT_DURATION_SECONDS,
)
from app.api.v1.monitoring.metric_utils import normalize_marketplace_label
from app.api.v1.monitoring.models import (
    MonitoringTarget,
    MonitoringTargetStatus,
    ProductCacheEntry,
    SnapshotSource,
)
from app.api.v1.monitoring.services.fetcher_client import (
    FetchedProductData,
    MonitoringFetcherClient,
    MonitoringFetcherTemporarilyUnavailableError,
    build_monitoring_fetcher_client,
)
from app.core.logging import get_logger


logger = get_logger(__name__)

MIN_PRODUCT_CACHE_MINUTES = 60
PRODUCT_CACHE_LOCK_TTL_SECONDS = 480
PRODUCT_CACHE_WAIT_SECONDS = 5.0
PRODUCT_CACHE_WAIT_INTERVAL_SECONDS = 0.5


class ProductCacheError(Exception):
    pass


class ProductCacheBusyError(ProductCacheError):
    pass


@dataclass(frozen=True, slots=True)
class ProductCacheResult:
    product: FetchedProductData
    source: str
    is_stale: bool
    parsed_at: datetime
    expires_at: datetime
    effective_cache_minutes: int

    def build_snapshot_raw_data(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "cache": {
                "source": self.source,
                "is_stale": self.is_stale,
                "parsed_at": self.parsed_at.isoformat(),
                "expires_at": self.expires_at.isoformat(),
                "effective_cache_minutes": self.effective_cache_minutes,
            },
            "product": self.product.raw_data,
        }


class ProductCacheService:
    def __init__(
            self,
            *,
            fetcher_client: MonitoringFetcherClient | None = None,
            lock_ttl_seconds: int = PRODUCT_CACHE_LOCK_TTL_SECONDS,
            wait_seconds: float = PRODUCT_CACHE_WAIT_SECONDS,
            wait_interval_seconds: float = PRODUCT_CACHE_WAIT_INTERVAL_SECONDS,
    ) -> None:
        self.fetcher_client = fetcher_client or build_monitoring_fetcher_client()
        self.lock_ttl_seconds = lock_ttl_seconds
        self.wait_seconds = wait_seconds
        self.wait_interval_seconds = wait_interval_seconds

    def get_or_refresh_product(
            self,
            *,
            target: MonitoringTarget,
            force_refresh: bool = False,
            interactive: bool = False,
    ) -> ProductCacheResult:
        return self.get_or_refresh_product_by_identity(
            marketplace=target.marketplace,
            url=target.url,
            external_id=target.external_id,
            title=target.title,
            seller_name=target.seller_name,
            brand=target.brand,
            fallback_interval_minutes=target.check_interval_minutes,
            force_refresh=force_refresh,
            log_identity=str(target.id),
            fetch_product_callback=lambda: self.fetcher_client.fetch_target(target=target, interactive=interactive),
        )

    def get_or_refresh_product_by_identity(
            self,
            *,
            marketplace: str,
            url: str,
            external_id: str = "",
            title: str = "",
            seller_name: str = "",
            brand: str = "",
            fallback_interval_minutes: int | None = None,
            force_refresh: bool = False,
            log_identity: str = "",
            interactive: bool = False,
            fetch_product_callback: Callable[[], FetchedProductData] | None = None,
    ) -> ProductCacheResult:
        normalized_external_id = external_id.strip()
        marketplace_label = normalize_marketplace_label(marketplace)

        cache_entry = self._get_cache_entry(
            marketplace=marketplace,
            external_id=normalized_external_id,
            url=url,
        )

        self._record_lookup(
            marketplace_label=marketplace_label,
            operation="get_or_refresh",
            cache_entry=cache_entry,
        )

        effective_cache_minutes = self.calculate_effective_cache_minutes(
            marketplace=marketplace,
            external_id=normalized_external_id,
            fallback_interval_minutes=fallback_interval_minutes,
        )

        if cache_entry is not None and not force_refresh:
            fresh_result = self._build_fresh_cache_result_if_possible(
                cache_entry=cache_entry,
                effective_cache_minutes=effective_cache_minutes,
            )
            if fresh_result is not None:
                self._record_request_result(
                    marketplace_label=marketplace_label,
                    operation="get_or_refresh",
                    result="fresh_hit",
                )
                self._observe_cache_result(
                    marketplace_label=marketplace_label,
                    cache_result=fresh_result,
                )
                return fresh_result

        lock_key = self._build_lock_key(
            marketplace=marketplace,
            external_id=normalized_external_id,
            url=url,
        )

        lock_acquired = False

        try:
            with RedisLock(
                    key=lock_key,
                    ttl=self.lock_ttl_seconds,
            ):
                lock_acquired = True

                MONITORING_PRODUCT_CACHE_LOCK_EVENTS_TOTAL.labels(
                    marketplace=marketplace_label,
                    result="acquired",
                ).inc()

                return self._refresh_product_under_lock(
                    marketplace=marketplace,
                    url=url,
                    external_id=normalized_external_id,
                    title=title,
                    seller_name=seller_name,
                    brand=brand,
                    fallback_interval_minutes=fallback_interval_minutes,
                    force_refresh=force_refresh,
                    log_identity=log_identity,
                    interactive=interactive,
                    fetch_product_callback=fetch_product_callback,
                )

        except RedisLockAlreadyAcquiredError as exc:
            MONITORING_PRODUCT_CACHE_LOCK_EVENTS_TOTAL.labels(
                marketplace=marketplace_label,
                result="busy",
            ).inc()

            stale_result = self._build_stale_cache_result_if_possible(
                cache_entry=cache_entry,
                effective_cache_minutes=effective_cache_minutes,
            )
            if stale_result is not None:
                logger.info(
                    "monitoring product cache returned stale data because refresh lock is busy",
                    extra={
                        "service": "monitoring_product_cache",
                        "marketplace": marketplace,
                        "external_id": normalized_external_id,
                        "lock_key": lock_key,
                    },
                )

                MONITORING_PRODUCT_CACHE_LOCK_EVENTS_TOTAL.labels(
                    marketplace=marketplace_label,
                    result="stale_served",
                ).inc()
                self._record_request_result(
                    marketplace_label=marketplace_label,
                    operation="get_or_refresh",
                    result="stale_hit",
                )
                self._observe_cache_result(
                    marketplace_label=marketplace_label,
                    cache_result=stale_result,
                )
                return stale_result

            wait_started_at = time.monotonic()
            waited_result = self._wait_for_cache_after_busy_lock(
                marketplace=marketplace,
                external_id=normalized_external_id,
                url=url,
                fallback_interval_minutes=fallback_interval_minutes,
            )
            wait_duration = time.monotonic() - wait_started_at

            if waited_result is not None:
                wait_result = (
                    "stale"
                    if waited_result.is_stale
                    else "fresh"
                )
                request_result = (
                    "wait_stale_hit"
                    if waited_result.is_stale
                    else "wait_fresh_hit"
                )

                MONITORING_PRODUCT_CACHE_WAIT_DURATION_SECONDS.labels(
                    marketplace=marketplace_label,
                    result=wait_result,
                ).observe(wait_duration)
                MONITORING_PRODUCT_CACHE_LOCK_EVENTS_TOTAL.labels(
                    marketplace=marketplace_label,
                    result=f"wait_{wait_result}",
                ).inc()
                self._record_request_result(
                    marketplace_label=marketplace_label,
                    operation="get_or_refresh",
                    result=request_result,
                )
                self._observe_cache_result(
                    marketplace_label=marketplace_label,
                    cache_result=waited_result,
                )
                return waited_result

            MONITORING_PRODUCT_CACHE_WAIT_DURATION_SECONDS.labels(
                marketplace=marketplace_label,
                result="timeout",
            ).observe(wait_duration)
            MONITORING_PRODUCT_CACHE_LOCK_EVENTS_TOTAL.labels(
                marketplace=marketplace_label,
                result="wait_timeout",
            ).inc()
            self._record_request_result(
                marketplace_label=marketplace_label,
                operation="get_or_refresh",
                result="busy",
            )

            raise ProductCacheBusyError(
                "Product cache refresh is already in progress and cached data is not ready yet."
            ) from exc

        except Exception:
            if not lock_acquired:
                MONITORING_PRODUCT_CACHE_LOCK_EVENTS_TOTAL.labels(
                    marketplace=marketplace_label,
                    result="error",
                ).inc()
                self._record_request_result(
                    marketplace_label=marketplace_label,
                    operation="get_or_refresh",
                    result="lock_error",
                )
            raise

    def get_cached_product_by_identity(
            self,
            *,
            marketplace: str,
            url: str,
            external_id: str = "",
            fallback_interval_minutes: int | None = None,
            allow_stale: bool = False,
    ) -> ProductCacheResult | None:
        """
        Return cached product data without contacting the parser.

        Telegram preview already refreshes ProductCacheEntry. During
        confirmation this method reuses the same cache row to build the
        initial snapshot instead of executing a second marketplace request.
        """

        normalized_external_id = external_id.strip()
        marketplace_label = normalize_marketplace_label(marketplace)
        cache_entry = self._get_cache_entry(
            marketplace=marketplace,
            external_id=normalized_external_id,
            url=url,
        )

        self._record_lookup(
            marketplace_label=marketplace_label,
            operation="get_cached",
            cache_entry=cache_entry,
        )

        if cache_entry is None:
            self._record_request_result(
                marketplace_label=marketplace_label,
                operation="get_cached",
                result="miss",
            )
            return None

        if (
            normalized_external_id
            and not self._cache_entry_matches_requested_url(
                cache_entry=cache_entry,
                requested_url=url,
            )
        ):
            logger.warning(
                "monitoring product cache identity mismatch",
                extra={
                    "service": "monitoring_product_cache",
                    "marketplace": marketplace,
                    "external_id": normalized_external_id,
                    "requested_url": url,
                    "cached_url": str(getattr(cache_entry, "url", "") or ""),
                },
            )
            self._record_request_result(
                marketplace_label=marketplace_label,
                operation="get_cached",
                result="identity_mismatch",
            )
            return None

        effective_cache_minutes = self.calculate_effective_cache_minutes(
            marketplace=marketplace,
            external_id=(
                normalized_external_id
                or cache_entry.external_id
            ),
            fallback_interval_minutes=fallback_interval_minutes,
        )

        fresh_result = self._build_fresh_cache_result_if_possible(
            cache_entry=cache_entry,
            effective_cache_minutes=effective_cache_minutes,
        )
        if fresh_result is not None:
            self._record_request_result(
                marketplace_label=marketplace_label,
                operation="get_cached",
                result="fresh_hit",
            )
            self._observe_cache_result(
                marketplace_label=marketplace_label,
                cache_result=fresh_result,
            )
            return fresh_result

        if not allow_stale:
            self._record_request_result(
                marketplace_label=marketplace_label,
                operation="get_cached",
                result="expired",
            )
            return None

        stale_result = self._build_stale_cache_result_if_possible(
            cache_entry=cache_entry,
            effective_cache_minutes=effective_cache_minutes,
        )

        if stale_result is not None:
            self._record_request_result(
                marketplace_label=marketplace_label,
                operation="get_cached",
                result="stale_hit",
            )
            self._observe_cache_result(
                marketplace_label=marketplace_label,
                cache_result=stale_result,
            )
            return stale_result

        self._record_request_result(
            marketplace_label=marketplace_label,
            operation="get_cached",
            result="miss",
        )
        return None

    def calculate_effective_cache_minutes(
            self,
            *,
            marketplace: str,
            external_id: str,
            fallback_interval_minutes: int | None = None,
    ) -> int:
        normalized_external_id = external_id.strip()

        min_interval = None
        if normalized_external_id:
            min_interval = (
                MonitoringTarget.objects
                .filter(
                    marketplace=marketplace,
                    external_id=normalized_external_id,
                    is_active=True,
                    status=MonitoringTargetStatus.ACTIVE,
                )
                .aggregate(min_interval=Min("check_interval_minutes"))
                .get("min_interval")
            )

        interval = min_interval or fallback_interval_minutes or MIN_PRODUCT_CACHE_MINUTES
        return max(MIN_PRODUCT_CACHE_MINUTES, int(interval))

    def _refresh_product_under_lock(
            self,
            *,
            marketplace: str,
            url: str,
            external_id: str,
            title: str,
            seller_name: str,
            brand: str,
            fallback_interval_minutes: int | None,
            force_refresh: bool,
            log_identity: str,
            interactive: bool,
            fetch_product_callback: Callable[[], FetchedProductData] | None,
    ) -> ProductCacheResult:
        marketplace_label = normalize_marketplace_label(marketplace)
        cache_entry = self._get_cache_entry(
            marketplace=marketplace,
            external_id=external_id,
            url=url,
        )

        effective_cache_minutes = self.calculate_effective_cache_minutes(
            marketplace=marketplace,
            external_id=external_id,
            fallback_interval_minutes=fallback_interval_minutes,
        )

        if cache_entry is not None and not force_refresh:
            fresh_result = self._build_fresh_cache_result_if_possible(
                cache_entry=cache_entry,
                effective_cache_minutes=effective_cache_minutes,
            )
            if fresh_result is not None:
                self._record_request_result(
                    marketplace_label=marketplace_label,
                    operation="get_or_refresh",
                    result="fresh_hit_after_lock",
                )
                self._observe_cache_result(
                    marketplace_label=marketplace_label,
                    cache_result=fresh_result,
                )
                return fresh_result

        refresh_started_at = time.monotonic()
        refresh_result = "error"

        MONITORING_PRODUCT_CACHE_REFRESHES_IN_PROGRESS.labels(
            marketplace=marketplace_label,
        ).inc()

        try:
            if fetch_product_callback is not None:
                fetched_product = fetch_product_callback()
            else:
                fetched_product = self.fetcher_client.fetch_product(
                    marketplace=marketplace,
                    url=url,
                    external_id=external_id,
                    title=title,
                    seller_name=seller_name,
                    brand=brand,
                    log_identity=log_identity,
                    interactive=interactive,
                )

            parsed_at = timezone.now()
            effective_cache_minutes = (
                self.calculate_effective_cache_minutes(
                    marketplace=marketplace,
                    external_id=fetched_product.external_id,
                    fallback_interval_minutes=fallback_interval_minutes,
                )
            )
            expires_at = parsed_at + timedelta(
                minutes=effective_cache_minutes
            )

            with transaction.atomic():
                cache_entry, _ = ProductCacheEntry.objects.update_or_create(
                    marketplace=marketplace,
                    external_id=fetched_product.external_id,
                    defaults={
                        "url": url,
                        "title": fetched_product.title,
                        "seller_name": fetched_product.seller_name,
                        "brand": fetched_product.brand,
                        "data": self._serialize_product_data(
                            product=fetched_product
                        ),
                        "parsed_at": parsed_at,
                        "expires_at": expires_at,
                        "effective_cache_minutes": (
                            effective_cache_minutes
                        ),
                        "last_success_at": parsed_at,
                        "last_error": "",
                    },
                )

            logger.info(
                "monitoring product cache refreshed from parser",
                extra={
                    "service": "monitoring_product_cache",
                    "marketplace": marketplace,
                    "external_id": fetched_product.external_id,
                    "cache_entry_id": str(cache_entry.id),
                    "effective_cache_minutes": (
                        effective_cache_minutes
                    ),
                    "log_identity": log_identity,
                },
            )

            refresh_result = "success"
            self._record_request_result(
                marketplace_label=marketplace_label,
                operation="get_or_refresh",
                result="refreshed",
            )

            return ProductCacheResult(
                product=fetched_product,
                source=SnapshotSource.PARSER,
                is_stale=False,
                parsed_at=parsed_at,
                expires_at=expires_at,
                effective_cache_minutes=effective_cache_minutes,
            )

        except MonitoringFetcherTemporarilyUnavailableError:
            refresh_result = "temporarily_unavailable"
            self._record_request_result(
                marketplace_label=marketplace_label,
                operation="get_or_refresh",
                result="temporarily_unavailable",
            )
            raise

        except Exception as exc:
            self._mark_cache_refresh_failed(
                cache_entry=cache_entry,
                error=str(exc),
            )
            self._record_request_result(
                marketplace_label=marketplace_label,
                operation="get_or_refresh",
                result="refresh_error",
            )
            raise

        finally:
            MONITORING_PRODUCT_CACHE_REFRESHES_TOTAL.labels(
                marketplace=marketplace_label,
                result=refresh_result,
            ).inc()
            MONITORING_PRODUCT_CACHE_REFRESH_DURATION_SECONDS.labels(
                marketplace=marketplace_label,
                result=refresh_result,
            ).observe(
                time.monotonic() - refresh_started_at
            )
            MONITORING_PRODUCT_CACHE_REFRESHES_IN_PROGRESS.labels(
                marketplace=marketplace_label,
            ).dec()

    def _get_cache_entry(
            self,
            *,
            marketplace: str,
            external_id: str,
            url: str,
    ) -> ProductCacheEntry | None:
        normalized_external_id = external_id.strip()

        if normalized_external_id:
            cache_entry = (
                ProductCacheEntry.objects
                .filter(
                    marketplace=marketplace,
                    external_id=normalized_external_id,
                )
                .first()
            )
            if cache_entry is not None:
                return cache_entry

        normalized_url = url.strip()
        if normalized_url:
            return (
                ProductCacheEntry.objects
                .filter(
                    marketplace=marketplace,
                    url=normalized_url,
                )
                .order_by("-updated_at")
                .first()
            )

        return None

    def _cache_entry_matches_requested_url(
            self,
            *,
            cache_entry: ProductCacheEntry,
            requested_url: str,
    ) -> bool:
        normalized_requested_url = self._normalize_product_url(
            requested_url
        )
        cached_url = str(
            getattr(cache_entry, "url", "") or ""
        )
        normalized_cached_url = self._normalize_product_url(
            cached_url
        )

        if (
            normalized_requested_url
            and normalized_cached_url
            and normalized_requested_url == normalized_cached_url
        ):
            return True

        external_id = str(
            getattr(cache_entry, "external_id", "") or ""
        ).strip()
        if not external_id:
            return False

        requested_path = unquote(
            urlsplit(requested_url.strip()).path
        )
        external_id_pattern = (
            rf"(?<![A-Za-z0-9])"
            rf"{re.escape(external_id)}"
            rf"(?![A-Za-z0-9])"
        )

        return (
            re.search(
                external_id_pattern,
                requested_path,
            )
            is not None
        )

    def _normalize_product_url(
            self,
            url: str,
    ) -> str:
        normalized_url = url.strip()
        if not normalized_url:
            return ""

        parsed_url = urlsplit(normalized_url)
        normalized_host = (
            parsed_url.netloc
            .lower()
            .removeprefix("www.")
        )
        normalized_path = (
            unquote(parsed_url.path)
            .rstrip("/")
        )

        if normalized_host:
            return f"{normalized_host}{normalized_path}"

        return normalized_path or normalized_url

    def _build_fresh_cache_result_if_possible(
            self,
            *,
            cache_entry: ProductCacheEntry,
            effective_cache_minutes: int,
    ) -> ProductCacheResult | None:
        expires_at = self._calculate_actual_expires_at(
            cache_entry=cache_entry,
            effective_cache_minutes=effective_cache_minutes,
        )
        self._sync_cache_expiration_metadata_if_needed(
            cache_entry=cache_entry,
            effective_cache_minutes=effective_cache_minutes,
            expires_at=expires_at,
        )

        if expires_at <= timezone.now():
            return None

        return ProductCacheResult(
            product=self._deserialize_product_data(cache_entry=cache_entry),
            source=SnapshotSource.CACHE,
            is_stale=False,
            parsed_at=cache_entry.parsed_at,
            expires_at=expires_at,
            effective_cache_minutes=effective_cache_minutes,
        )

    def _build_stale_cache_result_if_possible(
            self,
            *,
            cache_entry: ProductCacheEntry | None,
            effective_cache_minutes: int,
    ) -> ProductCacheResult | None:
        if cache_entry is None:
            return None

        expires_at = self._calculate_actual_expires_at(
            cache_entry=cache_entry,
            effective_cache_minutes=effective_cache_minutes,
        )
        self._sync_cache_expiration_metadata_if_needed(
            cache_entry=cache_entry,
            effective_cache_minutes=effective_cache_minutes,
            expires_at=expires_at,
        )

        return ProductCacheResult(
            product=self._deserialize_product_data(cache_entry=cache_entry),
            source=SnapshotSource.STALE_CACHE,
            is_stale=True,
            parsed_at=cache_entry.parsed_at,
            expires_at=expires_at,
            effective_cache_minutes=effective_cache_minutes,
        )

    def _wait_for_cache_after_busy_lock(
            self,
            *,
            marketplace: str,
            external_id: str,
            url: str,
            fallback_interval_minutes: int | None,
    ) -> ProductCacheResult | None:
        deadline = time.monotonic() + self.wait_seconds

        while time.monotonic() < deadline:
            time.sleep(self.wait_interval_seconds)

            cache_entry = self._get_cache_entry(
                marketplace=marketplace,
                external_id=external_id,
                url=url,
            )
            if cache_entry is None:
                continue

            effective_cache_minutes = self.calculate_effective_cache_minutes(
                marketplace=marketplace,
                external_id=cache_entry.external_id,
                fallback_interval_minutes=fallback_interval_minutes,
            )

            fresh_result = self._build_fresh_cache_result_if_possible(
                cache_entry=cache_entry,
                effective_cache_minutes=effective_cache_minutes,
            )
            if fresh_result is not None:
                return fresh_result

            return self._build_stale_cache_result_if_possible(
                cache_entry=cache_entry,
                effective_cache_minutes=effective_cache_minutes,
            )

        return None

    def _calculate_actual_expires_at(
            self,
            *,
            cache_entry: ProductCacheEntry,
            effective_cache_minutes: int,
    ) -> datetime:
        return cache_entry.parsed_at + timedelta(minutes=effective_cache_minutes)

    def _sync_cache_expiration_metadata_if_needed(
            self,
            *,
            cache_entry: ProductCacheEntry,
            effective_cache_minutes: int,
            expires_at: datetime,
    ) -> None:
        if (
                cache_entry.effective_cache_minutes == effective_cache_minutes
                and cache_entry.expires_at == expires_at
        ):
            return

        cache_entry.effective_cache_minutes = effective_cache_minutes
        cache_entry.expires_at = expires_at
        cache_entry.save(
            update_fields=[
                "effective_cache_minutes",
                "expires_at",
                "updated_at",
            ]
        )

    def _record_lookup(
            self,
            *,
            marketplace_label: str,
            operation: str,
            cache_entry: ProductCacheEntry | None,
    ) -> None:
        result = "hit" if cache_entry is not None else "miss"

        MONITORING_PRODUCT_CACHE_LOOKUPS_TOTAL.labels(
            marketplace=marketplace_label,
            operation=operation,
            result=result,
        ).inc()

    def _record_request_result(
            self,
            *,
            marketplace_label: str,
            operation: str,
            result: str,
    ) -> None:
        MONITORING_PRODUCT_CACHE_REQUESTS_TOTAL.labels(
            marketplace=marketplace_label,
            operation=operation,
            result=result,
        ).inc()

    def _observe_cache_result(
            self,
            *,
            marketplace_label: str,
            cache_result: ProductCacheResult,
    ) -> None:
        parsed_at = getattr(cache_result, "parsed_at", None)
        if parsed_at is None:
            return

        source = str(
            getattr(cache_result, "source", "unknown")
            or "unknown"
        )
        age_seconds = max(
            0.0,
            (timezone.now() - parsed_at).total_seconds(),
        )

        MONITORING_PRODUCT_CACHE_ENTRY_AGE_SECONDS.labels(
            marketplace=marketplace_label,
            source=source,
        ).observe(age_seconds)

    def _serialize_product_data(
            self,
            *,
            product: FetchedProductData,
    ) -> dict[str, Any]:
        return {
            "external_id": product.external_id,
            "title": product.title,
            "seller_name": product.seller_name,
            "brand": product.brand,
            "price": self._decimal_to_json_value(product.price),
            "old_price": self._decimal_to_json_value(product.old_price),
            "currency": product.currency,
            "is_available": product.is_available,
            "rating": self._decimal_to_json_value(product.rating),
            "reviews_count": product.reviews_count,
            "raw_data": product.raw_data,
        }

    def _deserialize_product_data(
            self,
            *,
            cache_entry: ProductCacheEntry,
    ) -> FetchedProductData:
        data = cache_entry.data or {}

        return FetchedProductData(
            external_id=str(data.get("external_id") or cache_entry.external_id),
            title=str(data.get("title") or cache_entry.title or ""),
            seller_name=str(data.get("seller_name") or cache_entry.seller_name or ""),
            brand=str(data.get("brand") or cache_entry.brand or ""),
            price=self._decimal_from_json_value(data.get("price")),
            old_price=self._decimal_from_json_value(data.get("old_price")),
            currency=str(data.get("currency") or "RUB"),
            is_available=data.get("is_available"),
            rating=self._decimal_from_json_value(data.get("rating")),
            reviews_count=self._int_from_json_value(data.get("reviews_count")),
            raw_data={
                "source": "product_cache_entry",
                "cache_entry_id": str(cache_entry.id),
                "cached_raw_data": data.get("raw_data") or {},
            },
        )

    def _mark_cache_refresh_failed(
            self,
            *,
            cache_entry: ProductCacheEntry | None,
            error: str,
    ) -> None:
        if cache_entry is None:
            return

        cache_entry.last_error = error
        cache_entry.save(
            update_fields=[
                "last_error",
                "updated_at",
            ]
        )

    def _build_lock_key(
            self,
            *,
            marketplace: str,
            external_id: str,
            url: str,
    ) -> str:
        normalized_external_id = external_id.strip()
        if normalized_external_id:
            identity = f"external_id:{normalized_external_id}"
        else:
            url_hash = hashlib.sha256(url.strip().encode("utf-8")).hexdigest()
            identity = f"url_hash:{url_hash}"

        return f"monitoring:product_cache:refresh:{marketplace}:{identity}"

    def _decimal_to_json_value(self, value: Decimal | None) -> str | None:
        if value is None:
            return None

        return str(value)

    def _decimal_from_json_value(self, value: Any) -> Decimal | None:
        if value is None or value == "":
            return None

        return Decimal(str(value))

    def _int_from_json_value(self, value: Any) -> int | None:
        if value is None or value == "":
            return None

        return int(value)
