from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from app.api.v1.monitoring.models import (
    MonitoringTarget,
    MonitoringTargetRole,
    MonitoringTargetStatus,
    ProductSnapshot,
    SnapshotParseStatus,
)
from app.api.v1.monitoring.services.fetcher_client import (
    MonitoringFetcherError,
    build_monitoring_fetcher_client,
)
from app.api.v1.monitoring.services.product_cache import (
    ProductCacheResult,
    ProductCacheService,
)
from app.api.v1.monitoring.services.scanner import MonitoringScanner
from app.api.v1.monitoring.services.snapshot_service import (
    create_product_snapshot,
)
from app.api.v1.monitoring.services.target_resolver import (
    resolve_monitoring_target,
)
from app.core.logging import get_logger


logger = get_logger(__name__)


class MonitoringTargetServiceError(Exception):
    """Base monitoring target service error."""


class MonitoringTargetNotFoundError(
    MonitoringTargetServiceError,
):
    """Monitoring target does not exist or belongs to another user."""


class MonitoringTargetCheckError(
    MonitoringTargetServiceError,
):
    """Manual monitoring target check failed."""


class MonitoringTargetCheckBusyError(
    MonitoringTargetCheckError,
):
    """Another process is currently refreshing the same product."""


class MonitoringTargetCheckTemporarilyUnavailableError(
    MonitoringTargetCheckError,
):
    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: int,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(1, int(retry_after_seconds))


class MonitoringTargetUpdateError(
    MonitoringTargetServiceError,
):
    """Monitoring target update data is invalid."""


@dataclass(frozen=True, slots=True)
class MonitoringTargetCheckResult:
    target: MonitoringTarget
    snapshot: ProductSnapshot
    alerts_count: int
    cache_source: str
    cache_is_stale: bool
    effective_cache_minutes: int


def create_monitoring_target(
    *,
    user,
    validated_data: dict[str, Any],
) -> MonitoringTarget:
    role = validated_data.get(
        "role",
        MonitoringTargetRole.COMPETITOR,
    )
    check_interval_minutes = validated_data.get(
        "check_interval_minutes",
        60,
    )
    external_id = str(
        validated_data.get("external_id") or ""
    ).strip()
    requested_url = str(
        validated_data["url"]
    ).strip()

    resolved_target = resolve_monitoring_target(
        user=user,
        marketplace=validated_data["marketplace"],
        url=requested_url,
        role=role,
        check_interval_minutes=check_interval_minutes,
    )

    target = resolved_target.target
    created = resolved_target.created

    cache_result = ProductCacheService().get_cached_product_by_identity(
        marketplace=target.marketplace,
        url=requested_url,
        external_id=external_id or target.external_id,
        fallback_interval_minutes=target.check_interval_minutes,
        allow_stale=True,
    )

    if cache_result is not None:
        _create_initial_snapshot_from_cache(
            target=target,
            cache_result=cache_result,
        )
    else:
        _create_initial_snapshot_from_fetcher(
            target=target,
        )

    target.refresh_from_db()

    logger.info(
        "monitoring target resolved",
        extra={
            "service": "monitoring",
            "target_id": str(target.id),
            "user_id": str(user.id),
            "marketplace": target.marketplace,
            "target_created": created,
            "initial_snapshot_source": (
                cache_result.source
                if cache_result is not None
                else "parser_fallback"
            ),
        },
    )

    return target


def _create_initial_snapshot_from_cache(
    *,
    target: MonitoringTarget,
    cache_result: ProductCacheResult,
) -> ProductSnapshot:
    product = cache_result.product

    return create_product_snapshot(
        target=target,
        parse_status=SnapshotParseStatus.SUCCESS,
        source=cache_result.source,
        price=product.price,
        old_price=product.old_price,
        currency=product.currency,
        is_available=product.is_available,
        rating=product.rating,
        reviews_count=product.reviews_count,
        title=product.title,
        seller_name=product.seller_name,
        brand=product.brand,
        external_id=product.external_id,
        raw_data=cache_result.build_snapshot_raw_data(),
        error_message="",
        checked_at=cache_result.parsed_at,
    )


def _create_initial_snapshot_from_fetcher(
    *,
    target: MonitoringTarget,
) -> ProductSnapshot:
    fetcher_client = build_monitoring_fetcher_client()

    try:
        fetched_product = fetcher_client.fetch_target(
            target=target,
        )

        return create_product_snapshot(
            target=target,
            parse_status=SnapshotParseStatus.SUCCESS,
            price=fetched_product.price,
            old_price=fetched_product.old_price,
            currency=fetched_product.currency,
            is_available=fetched_product.is_available,
            rating=fetched_product.rating,
            reviews_count=fetched_product.reviews_count,
            title=fetched_product.title,
            seller_name=fetched_product.seller_name,
            brand=fetched_product.brand,
            external_id=fetched_product.external_id,
            raw_data=fetched_product.raw_data,
            error_message="",
        )

    except MonitoringFetcherError as exc:
        return create_product_snapshot(
            target=target,
            parse_status=SnapshotParseStatus.PARSE_ERROR,
            raw_data={
                "source": "go_fetcher",
                "error": str(exc),
            },
            error_message=str(exc),
        )


def get_monitoring_target_for_user(
    *,
    user,
    target_id: UUID | str,
) -> MonitoringTarget:
    try:
        return MonitoringTarget.objects.get(
            id=target_id,
            user=user,
        )

    except MonitoringTarget.DoesNotExist as exc:
        raise MonitoringTargetNotFoundError(
            "Monitoring target was not found."
        ) from exc


def update_monitoring_target(
    *,
    user,
    target_id: UUID | str,
    validated_data: dict[str, Any],
) -> MonitoringTarget:
    """
    Update user-editable monitoring target settings.

    Only the role and check interval can be changed. Marketplace and URL
    represent the product identity and therefore remain immutable.
    """

    allowed_fields = {
        "role",
        "check_interval_minutes",
    }

    unsupported_fields = set(validated_data) - allowed_fields

    if unsupported_fields:
        raise MonitoringTargetUpdateError(
            "Unsupported monitoring target fields: "
            f"{', '.join(sorted(unsupported_fields))}."
        )

    if not validated_data:
        raise MonitoringTargetUpdateError(
            "At least one field must be provided."
        )

    with transaction.atomic():
        try:
            target = (
                MonitoringTarget.objects
                .select_for_update()
                .get(
                    id=target_id,
                    user=user,
                )
            )

        except MonitoringTarget.DoesNotExist as exc:
            raise MonitoringTargetNotFoundError(
                "Monitoring target was not found."
            ) from exc

        update_fields: list[str] = []

        if "role" in validated_data:
            role = validated_data["role"]

            allowed_roles = {
                choice[0]
                for choice in MonitoringTargetRole.choices
            }

            if role not in allowed_roles:
                raise MonitoringTargetUpdateError(
                    "Unsupported monitoring target role."
                )

            target.role = role
            update_fields.append("role")

        if "check_interval_minutes" in validated_data:
            check_interval_minutes = validated_data[
                "check_interval_minutes"
            ]

            if (
                check_interval_minutes < 15
                or check_interval_minutes > 1440
            ):
                raise MonitoringTargetUpdateError(
                    "Check interval must be between "
                    "15 and 1440 minutes."
                )

            target.check_interval_minutes = (
                check_interval_minutes
            )
            target.next_check_at = _calculate_next_check_at(
                target=target,
                check_interval_minutes=check_interval_minutes,
            )

            update_fields.extend(
                [
                    "check_interval_minutes",
                    "next_check_at",
                ]
            )

        target.save(
            update_fields=[
                *update_fields,
                "updated_at",
            ]
        )

    logger.info(
        "monitoring target updated",
        extra={
            "service": "monitoring",
            "target_id": str(target.id),
            "user_id": str(user.id),
            "updated_fields": update_fields,
        },
    )

    return target


def pause_monitoring_target(
    *,
    user,
    target_id: UUID | str,
) -> MonitoringTarget:
    """
    Pause a monitoring target.

    The operation is idempotent. Repeated pause requests return the same
    paused target without changing its schedule or creating side effects.
    """

    with transaction.atomic():
        try:
            target = (
                MonitoringTarget.objects
                .select_for_update()
                .get(
                    id=target_id,
                    user=user,
                )
            )

        except MonitoringTarget.DoesNotExist as exc:
            raise MonitoringTargetNotFoundError(
                "Monitoring target was not found."
            ) from exc

        if (
            target.status == MonitoringTargetStatus.PAUSED
            and not target.is_active
        ):
            return target

        target.status = MonitoringTargetStatus.PAUSED
        target.is_active = False

        target.save(
            update_fields=[
                "status",
                "is_active",
                "updated_at",
            ]
        )

    logger.info(
        "monitoring target paused",
        extra={
            "service": "monitoring",
            "target_id": str(target.id),
            "user_id": str(user.id),
            "marketplace": target.marketplace,
        },
    )

    return target


def resume_monitoring_target(
    *,
    user,
    target_id: UUID | str,
) -> MonitoringTarget:
    """
    Resume a paused or failed monitoring target.

    Resumed targets become due immediately, so the scanner can process
    them during its nearest iteration.

    The operation is idempotent for targets that are already active.
    """

    with transaction.atomic():
        try:
            target = (
                MonitoringTarget.objects
                .select_for_update()
                .get(
                    id=target_id,
                    user=user,
                )
            )

        except MonitoringTarget.DoesNotExist as exc:
            raise MonitoringTargetNotFoundError(
                "Monitoring target was not found."
            ) from exc

        if (
            target.status == MonitoringTargetStatus.ACTIVE
            and target.is_active
        ):
            return target

        target.status = MonitoringTargetStatus.ACTIVE
        target.is_active = True
        target.next_check_at = timezone.now()
        target.last_error = ""

        target.save(
            update_fields=[
                "status",
                "is_active",
                "next_check_at",
                "last_error",
                "updated_at",
            ]
        )

    logger.info(
        "monitoring target resumed",
        extra={
            "service": "monitoring",
            "target_id": str(target.id),
            "user_id": str(user.id),
            "marketplace": target.marketplace,
        },
    )

    return target


def delete_monitoring_target(
    *,
    user,
    target_id: UUID | str,
) -> None:
    """
    Permanently delete a monitoring target and its dependent records.

    ProductSnapshot, Alert and target-specific AlertRule records are removed
    according to their model cascade configuration.
    """

    with transaction.atomic():
        try:
            target = (
                MonitoringTarget.objects
                .select_for_update()
                .get(
                    id=target_id,
                    user=user,
                )
            )

        except MonitoringTarget.DoesNotExist as exc:
            raise MonitoringTargetNotFoundError(
                "Monitoring target was not found."
            ) from exc

        deleted_target_id = target.id
        marketplace = target.marketplace

        target.delete()

    logger.info(
        "monitoring target deleted",
        extra={
            "service": "monitoring",
            "target_id": str(deleted_target_id),
            "user_id": str(user.id),
            "marketplace": marketplace,
        },
    )


def check_monitoring_target_now(
    *,
    user,
    target_id: UUID | str,
) -> MonitoringTargetCheckResult:
    """
    Run a manual check for an existing target.

    The function:
    - verifies target ownership;
    - does not create another MonitoringTarget;
    - requests a forced refresh through ProductCacheService;
    - keeps Redis locking and shared cache behavior;
    - creates ProductSnapshot;
    - creates alerts through snapshot service;
    - does not postpone the regular schedule when the cache lock is busy.
    """

    target = get_monitoring_target_for_user(
        user=user,
        target_id=target_id,
    )

    scanner = MonitoringScanner()

    process_result = scanner.process_target(
        target=target,
        force_refresh=True,
        postpone_on_busy=False,
        trigger="manual_check",
    )

    if process_result.temporarily_unavailable:
        retry_after_seconds = max(
            1,
            int(process_result.retry_after_seconds or 60),
        )
        raise MonitoringTargetCheckTemporarilyUnavailableError(
            _build_temporary_unavailable_message(
                error=process_result.error,
                retry_after_seconds=retry_after_seconds,
            ),
            retry_after_seconds=retry_after_seconds,
        )

    if process_result.busy:
        raise MonitoringTargetCheckBusyError(
            process_result.error
            or (
                "Product refresh is already in progress. "
                "Try again shortly."
            )
        )

    if not process_result.success:
        raise MonitoringTargetCheckError(
            process_result.error
            or "Monitoring target check failed."
        )

    snapshot = process_result.snapshot
    effective_cache_minutes = (
        process_result.effective_cache_minutes
    )

    if snapshot is None:
        raise MonitoringTargetCheckError(
            "Monitoring target check did not create a snapshot."
        )

    if effective_cache_minutes is None:
        raise MonitoringTargetCheckError(
            "Monitoring target check did not return cache metadata."
        )

    target.refresh_from_db()

    logger.info(
        "monitoring target checked manually",
        extra={
            "service": "monitoring",
            "target_id": str(target.id),
            "user_id": str(user.id),
            "marketplace": target.marketplace,
            "snapshot_id": str(snapshot.id),
            "alerts_count": process_result.alerts_count,
            "cache_source": process_result.cache_source,
            "cache_is_stale": process_result.cache_is_stale,
            "effective_cache_minutes": effective_cache_minutes,
        },
    )

    return MonitoringTargetCheckResult(
        target=target,
        snapshot=snapshot,
        alerts_count=process_result.alerts_count,
        cache_source=process_result.cache_source,
        cache_is_stale=process_result.cache_is_stale,
        effective_cache_minutes=effective_cache_minutes,
    )


def _calculate_next_check_at(
    *,
    target: MonitoringTarget,
    check_interval_minutes: int,
):
    """
    Recalculate the schedule when the user changes the interval.

    If the previous calculated moment is already in the past, the target
    becomes due immediately.
    """

    now = timezone.now()

    if target.last_checked_at is None:
        return now

    calculated_next_check_at = (
        target.last_checked_at
        + timedelta(minutes=check_interval_minutes)
    )

    if calculated_next_check_at <= now:
        return now

    return calculated_next_check_at


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
