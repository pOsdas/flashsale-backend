from dataclasses import dataclass
from datetime import timedelta
import time

from django.db import transaction
from django.utils import timezone

from app.api.v1.monitoring.models import (
    MonitoringTarget,
    MonitoringTargetStatus,
    ProductSnapshot,
    SnapshotParseStatus,
)
from app.api.v1.monitoring.services.fetcher_client import (
    MonitoringFetcherTemporarilyUnavailableError,
)
from app.api.v1.monitoring.services.product_cache import (
    ProductCacheBusyError,
    ProductCacheService,
)
from app.api.v1.monitoring.services.snapshot_service import (
    create_product_snapshot,
)
from app.api.v1.monitoring.metrics import (
    MONITORING_ALERTS_CREATED_TOTAL,
    MONITORING_CACHE_RESULTS_TOTAL,
    MONITORING_SCANNER_DUE_TARGETS,
    MONITORING_SCANNER_ITERATION_DURATION_SECONDS,
    MONITORING_SCANNER_ITERATIONS_TOTAL,
    MONITORING_SCANNER_LAST_PROCESSED_TARGETS,
    MONITORING_SCANNER_LAST_SUCCESS_TIMESTAMP_SECONDS,
    MONITORING_SCANNER_OLDEST_OVERDUE_AGE_SECONDS,
    MONITORING_SCANNER_OVERDUE_TARGETS,
    MONITORING_SNAPSHOTS_CREATED_TOTAL,
    MONITORING_TARGET_PROCESSING_DURATION_SECONDS,
    MONITORING_TARGET_PROCESSING_TOTAL,
)
from app.core.logging import get_logger


logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class MonitoringTargetProcessResult:
    """
    Result of a single monitoring target processing attempt.

    The scanner worker, REST API and Telegram bot can use the same
    processing method without duplicating the product fetching,
    snapshot creation and alert detection logic.
    """

    success: bool
    snapshot: ProductSnapshot | None = None
    alerts_count: int = 0
    cache_source: str = ""
    cache_is_stale: bool = False
    effective_cache_minutes: int | None = None
    error: str = ""
    busy: bool = False
    temporarily_unavailable: bool = False
    retry_after_seconds: int | None = None


class MonitoringScanner:
    def __init__(
            self,
            batch_size: int = 50,
            overdue_after_seconds: int = 300,
            product_cache_service: ProductCacheService | None = None,
    ) -> None:
        self.batch_size = batch_size
        self.overdue_after_seconds = overdue_after_seconds
        self.product_cache_service = (
                product_cache_service or ProductCacheService()
        )

    def run_once(self) -> int:
        started_at = time.monotonic()

        try:
            self._update_schedule_metrics()

            targets = self._get_due_targets()
            processed_count = 0
            processing_crashes = 0

            for target in targets:
                try:
                    self._process_target(target=target)
                except Exception as exc:
                    processing_crashes += 1
                    logger.exception(
                        "monitoring target processing crashed; scanner will continue",
                        extra={
                            "service": "monitoring_scanner",
                            "target_id": str(target.id),
                            "marketplace": target.marketplace,
                            "error": str(exc),
                            "trigger": "scanner",
                        },
                    )
                finally:
                    processed_count += 1

            MONITORING_SCANNER_LAST_PROCESSED_TARGETS.set(
                processed_count
            )

            iteration_status = (
                "partial_error"
                if processing_crashes
                else "success"
            )

            if not processing_crashes:
                MONITORING_SCANNER_LAST_SUCCESS_TIMESTAMP_SECONDS.set(
                    timezone.now().timestamp()
                )

            MONITORING_SCANNER_ITERATIONS_TOTAL.labels(
                status=iteration_status,
            ).inc()

            if processed_count:
                logger.info(
                    "monitoring scanner iteration finished",
                    extra={
                        "service": "monitoring_scanner",
                        "processed_count": processed_count,
                        "processing_crashes": processing_crashes,
                        "status": iteration_status,
                    },
                )

            return processed_count

        except Exception:
            MONITORING_SCANNER_ITERATIONS_TOTAL.labels(
                status="error",
            ).inc()

            logger.exception(
                "monitoring scanner iteration failed",
                extra={
                    "service": "monitoring_scanner",
                },
            )

            raise

        finally:
            duration = time.monotonic() - started_at
            MONITORING_SCANNER_ITERATION_DURATION_SECONDS.observe(
                duration
            )

    def _update_schedule_metrics(self) -> None:
        now = timezone.now()
        overdue_before = now - timedelta(
            seconds=self.overdue_after_seconds,
        )

        base_queryset = MonitoringTarget.objects.filter(
            is_active=True,
            status=MonitoringTargetStatus.ACTIVE,
        )

        due_targets_count = base_queryset.filter(
            next_check_at__lte=now,
        ).count()

        overdue_queryset = base_queryset.filter(
            next_check_at__lte=overdue_before,
        )

        overdue_targets_count = overdue_queryset.count()

        MONITORING_SCANNER_DUE_TARGETS.set(
            due_targets_count
        )
        MONITORING_SCANNER_OVERDUE_TARGETS.set(
            overdue_targets_count
        )

        oldest_overdue_target = (
            overdue_queryset
            .order_by("next_check_at")
            .only("next_check_at")
            .first()
        )

        if oldest_overdue_target is None:
            MONITORING_SCANNER_OLDEST_OVERDUE_AGE_SECONDS.set(
                0
            )
            return

        oldest_overdue_age_seconds = max(
            0.0,
            (
                    now - oldest_overdue_target.next_check_at
            ).total_seconds(),
        )

        MONITORING_SCANNER_OLDEST_OVERDUE_AGE_SECONDS.set(
            oldest_overdue_age_seconds
        )

    def _get_due_targets(self) -> list[MonitoringTarget]:
        now = timezone.now()

        return list(
            MonitoringTarget.objects
            .filter(
                is_active=True,
                status=MonitoringTargetStatus.ACTIVE,
                next_check_at__lte=now,
            )
            .order_by("next_check_at")[:self.batch_size]
        )

    def _process_target(
        self,
        *,
        target: MonitoringTarget,
    ) -> MonitoringTargetProcessResult:
        """
        Backward-compatible scanner worker entry point.

        Existing scanner tests or integrations may still call this private
        method, while REST API and Telegram bot use process_target().
        """

        return self.process_target(
            target=target,
            force_refresh=False,
            postpone_on_busy=True,
            trigger="scanner",
        )

    def process_target(
        self,
        *,
        target: MonitoringTarget,
        force_refresh: bool = False,
        postpone_on_busy: bool = True,
        trigger: str = "scanner",
    ) -> MonitoringTargetProcessResult:
        """
        Process one monitoring target.

        force_refresh=False:
            Use a fresh shared cache entry when available.

        force_refresh=True:
            Request a new marketplace fetch through ProductCacheService.
            Redis locking and shared cache updating are still preserved.

        postpone_on_busy=True:
            Move the next scheduled check five minutes forward when another
            process currently refreshes the same product.

        postpone_on_busy=False:
            Do not change the target schedule. This mode is used by manual
            check-now requests.
        """
        started_at = time.monotonic()

        marketplace_label = str(target.marketplace)
        trigger_label = trigger or "unknown"

        logger.info(
            "monitoring target processing started",
            extra={
                "service": "monitoring_scanner",
                "target_id": str(target.id),
                "marketplace": target.marketplace,
                "url": target.url,
                "force_refresh": force_refresh,
                "trigger": trigger,
            },
        )

        try:
            cache_result = (
                self.product_cache_service
                .get_or_refresh_product(
                    target=target,
                    force_refresh=force_refresh,
                )
            )
            fetched_data = cache_result.product

            snapshot = create_product_snapshot(
                target=target,
                parse_status=SnapshotParseStatus.SUCCESS,
                source=cache_result.source,
                external_id=fetched_data.external_id,
                price=fetched_data.price,
                old_price=fetched_data.old_price,
                currency=fetched_data.currency,
                is_available=fetched_data.is_available,
                rating=fetched_data.rating,
                reviews_count=fetched_data.reviews_count,
                title=fetched_data.title,
                seller_name=fetched_data.seller_name,
                brand=fetched_data.brand,
                raw_data=cache_result.build_snapshot_raw_data(),
            )

            target_still_exists = self._restore_target_after_success(
                target=target,
            )

            if not target_still_exists:
                return self._target_disappeared_result(
                    target=target,
                    trigger=trigger,
                    error=(
                        "Monitoring target was deleted after snapshot creation."
                    ),
                )

            alerts_count = snapshot.alerts.count()

            MONITORING_TARGET_PROCESSING_TOTAL.labels(
                marketplace=marketplace_label,
                trigger=trigger_label,
                result="success",
            ).inc()

            MONITORING_SNAPSHOTS_CREATED_TOTAL.labels(
                marketplace=marketplace_label,
                parse_status=SnapshotParseStatus.SUCCESS,
                trigger=trigger_label,
            ).inc()

            MONITORING_ALERTS_CREATED_TOTAL.labels(
                marketplace=marketplace_label,
                trigger=trigger_label,
            ).inc(alerts_count)

            MONITORING_CACHE_RESULTS_TOTAL.labels(
                marketplace=marketplace_label,
                source=str(cache_result.source),
                is_stale=str(cache_result.is_stale).lower(),
                trigger=trigger_label,
            ).inc()

            logger.info(
                "monitoring target processed successfully",
                extra={
                    "service": "monitoring_scanner",
                    "target_id": str(target.id),
                    "marketplace": target.marketplace,
                    "snapshot_id": str(snapshot.id),
                    "alerts_count": alerts_count,
                    "cache_source": cache_result.source,
                    "cache_is_stale": cache_result.is_stale,
                    "effective_cache_minutes": (
                        cache_result.effective_cache_minutes
                    ),
                    "force_refresh": force_refresh,
                    "trigger": trigger,
                },
            )

            return MonitoringTargetProcessResult(
                success=True,
                snapshot=snapshot,
                alerts_count=alerts_count,
                cache_source=cache_result.source,
                cache_is_stale=cache_result.is_stale,
                effective_cache_minutes=(
                    cache_result.effective_cache_minutes
                ),
            )

        except MonitoringFetcherTemporarilyUnavailableError as exc:
            logger.warning(
                "monitoring target processing postponed because marketplace gateway is temporarily unavailable",
                extra={
                    "service": "monitoring_scanner",
                    "target_id": str(target.id),
                    "marketplace": target.marketplace,
                    "error": str(exc),
                    "retry_after_seconds": exc.retry_after_seconds,
                    "postpone_on_busy": postpone_on_busy,
                    "trigger": trigger,
                },
            )

            if postpone_on_busy:
                target_still_exists = (
                    self._postpone_target_after_temporary_unavailability(
                        target=target,
                        error=str(exc),
                        retry_after_seconds=exc.retry_after_seconds,
                    )
                )

                if not target_still_exists:
                    return self._target_disappeared_result(
                        target=target,
                        trigger=trigger,
                        error=str(exc),
                    )

            MONITORING_TARGET_PROCESSING_TOTAL.labels(
                marketplace=marketplace_label,
                trigger=trigger_label,
                result="temporarily_unavailable",
            ).inc()

            return MonitoringTargetProcessResult(
                success=False,
                error=str(exc),
                busy=True,
                temporarily_unavailable=True,
                retry_after_seconds=exc.retry_after_seconds,
            )

        except ProductCacheBusyError as exc:
            logger.warning(
                "monitoring target processing postponed because product cache refresh is busy",
                extra={
                    "service": "monitoring_scanner",
                    "target_id": str(target.id),
                    "marketplace": target.marketplace,
                    "error": str(exc),
                    "postpone_on_busy": postpone_on_busy,
                    "trigger": trigger,
                },
            )

            if postpone_on_busy:
                target_still_exists = self._postpone_target_after_cache_busy(
                    target=target,
                    error=str(exc),
                )

                if not target_still_exists:
                    return self._target_disappeared_result(
                        target=target,
                        trigger=trigger,
                        error=str(exc),
                    )

            MONITORING_TARGET_PROCESSING_TOTAL.labels(
                marketplace=marketplace_label,
                trigger=trigger_label,
                result="busy",
            ).inc()

            return MonitoringTargetProcessResult(
                success=False,
                error=str(exc),
                busy=True,
            )

        except (
            MonitoringTarget.DoesNotExist,
            MonitoringTarget.NotUpdated,
        ) as exc:
            return self._target_disappeared_result(
                target=target,
                trigger=trigger,
                error=str(exc),
            )

        except Exception as exc:
            if not MonitoringTarget.objects.filter(id=target.id).exists():
                return self._target_disappeared_result(
                    target=target,
                    trigger=trigger,
                    error=str(exc),
                )

            logger.exception(
                "monitoring target processing failed",
                extra={
                    "service": "monitoring_scanner",
                    "target_id": str(target.id),
                    "marketplace": target.marketplace,
                    "error": str(exc),
                    "force_refresh": force_refresh,
                    "trigger": trigger,
                },
            )

            snapshot = self._mark_target_failed(
                target=target,
                error=str(exc),
            )

            if snapshot is None:
                return self._target_disappeared_result(
                    target=target,
                    trigger=trigger,
                    error=str(exc),
                )

            MONITORING_TARGET_PROCESSING_TOTAL.labels(
                marketplace=marketplace_label,
                trigger=trigger_label,
                result="error",
            ).inc()

            MONITORING_SNAPSHOTS_CREATED_TOTAL.labels(
                marketplace=marketplace_label,
                parse_status=SnapshotParseStatus.PARSE_ERROR,
                trigger=trigger_label,
            ).inc()

            return MonitoringTargetProcessResult(
                success=False,
                snapshot=snapshot,
                error=str(exc),
            )

        finally:
            duration = time.monotonic() - started_at

            MONITORING_TARGET_PROCESSING_DURATION_SECONDS.labels(
                marketplace=marketplace_label,
                trigger=trigger_label,
            ).observe(duration)

    def _target_disappeared_result(
        self,
        *,
        target: MonitoringTarget,
        trigger: str,
        error: str,
    ) -> MonitoringTargetProcessResult:
        trigger_label = trigger or "unknown"

        MONITORING_TARGET_PROCESSING_TOTAL.labels(
            marketplace=str(target.marketplace),
            trigger=trigger_label,
            result="deleted",
        ).inc()

        logger.info(
            "monitoring target disappeared during processing",
            extra={
                "service": "monitoring_scanner",
                "target_id": str(target.id),
                "marketplace": target.marketplace,
                "error": error,
                "trigger": trigger,
            },
        )

        return MonitoringTargetProcessResult(
            success=False,
            error="Monitoring target no longer exists.",
        )

    def _restore_target_after_success(
        self,
        *,
        target: MonitoringTarget,
    ) -> bool:
        """
        Restore an active target from FAILED after a successful manual retry.

        A paused target remains paused because is_active=False.
        """

        with transaction.atomic():
            locked_target = (
                MonitoringTarget.objects
                .select_for_update()
                .filter(id=target.id)
                .first()
            )

            if locked_target is None:
                return False

            if (
                locked_target.is_active
                and locked_target.status
                == MonitoringTargetStatus.FAILED
            ):
                locked_target.status = MonitoringTargetStatus.ACTIVE
                locked_target.save(
                    update_fields=[
                        "status",
                        "updated_at",
                    ]
                )

        return True

    def _postpone_target_after_temporary_unavailability(
        self,
        *,
        target: MonitoringTarget,
        error: str,
        retry_after_seconds: int,
    ) -> bool:
        retry_after_seconds = max(
            5,
            min(int(retry_after_seconds), 3600),
        )

        with transaction.atomic():
            locked_target = (
                MonitoringTarget.objects
                .select_for_update()
                .filter(id=target.id)
                .first()
            )

            if locked_target is None:
                return False

            locked_target.next_check_at = (
                timezone.now()
                + timedelta(seconds=retry_after_seconds)
            )
            locked_target.last_error = error
            locked_target.save(
                update_fields=[
                    "next_check_at",
                    "last_error",
                    "updated_at",
                ]
            )

        return True

    def _postpone_target_after_cache_busy(
        self,
        *,
        target: MonitoringTarget,
        error: str,
    ) -> bool:
        with transaction.atomic():
            locked_target = (
                MonitoringTarget.objects
                .select_for_update()
                .filter(id=target.id)
                .first()
            )

            if locked_target is None:
                return False

            locked_target.next_check_at = (
                timezone.now() + timedelta(minutes=5)
            )
            locked_target.last_error = error
            locked_target.save(
                update_fields=[
                    "next_check_at",
                    "last_error",
                    "updated_at",
                ]
            )

        return True

    def _mark_target_failed(
        self,
        *,
        target: MonitoringTarget,
        error: str,
    ) -> ProductSnapshot | None:
        """
        Save a failed snapshot.

        Active targets are moved to FAILED. Paused targets remain paused,
        because a manual check must not silently resume them.
        """

        with transaction.atomic():
            locked_target = (
                MonitoringTarget.objects
                .select_for_update()
                .filter(id=target.id)
                .first()
            )

            if locked_target is None:
                return None

            if locked_target.is_active:
                locked_target.status = MonitoringTargetStatus.FAILED
                locked_target.save(
                    update_fields=[
                        "status",
                        "updated_at",
                    ]
                )

            snapshot = create_product_snapshot(
                target=locked_target,
                parse_status=SnapshotParseStatus.PARSE_ERROR,
                error_message=error,
                raw_data={
                    "source": "monitoring_scanner",
                    "error": error,
                },
            )

        return snapshot
