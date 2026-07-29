from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.api.v1.monitoring.models import (
    MonitoringTarget,
    ProductSnapshot,
    SnapshotSource,
)
from app.api.v1.monitoring.services.fetcher_client import (
    MonitoringFetcherTemporarilyUnavailableError,
)
from app.api.v1.monitoring.services.scanner import (
    MonitoringScanner,
    MonitoringTargetProcessResult,
)
from app.api.v1.monitoring.services.snapshot_service import (
    create_product_snapshot,
)


class MonitoringScannerResilienceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="scanner-resilience-user",
        )
        self.target = MonitoringTarget.objects.create(
            user=self.user,
            marketplace="wb",
            url="https://www.wildberries.ru/catalog/123/detail.aspx",
            external_id="123",
        )

    def test_snapshot_is_not_created_for_deleted_target(self):
        stale_target = self.target
        stale_target_id = stale_target.id
        stale_target.delete()
        stale_target.id = stale_target_id

        with self.assertRaises(MonitoringTarget.DoesNotExist):
            create_product_snapshot(
                target=stale_target,
                price="100.00",
                title="Deleted target product",
            )

        self.assertEqual(ProductSnapshot.objects.count(), 0)

    @patch(
        "app.api.v1.monitoring.services.scanner."
        "create_product_snapshot"
    )
    def test_deleted_target_does_not_escape_process_target(
        self,
        mock_create_product_snapshot,
    ):
        mock_create_product_snapshot.side_effect = (
            MonitoringTarget.DoesNotExist(
                "Monitoring target was deleted before snapshot creation."
            )
        )

        cache_result = SimpleNamespace(
            product=SimpleNamespace(
                external_id="123",
                price="100.00",
                old_price=None,
                currency="RUB",
                is_available=True,
                rating=None,
                reviews_count=0,
                title="Product",
                seller_name="Seller",
                brand="Brand",
            ),
            source=SnapshotSource.PARSER,
            is_stale=False,
            effective_cache_minutes=60,
            build_snapshot_raw_data=Mock(return_value={}),
        )
        cache_service = Mock()
        cache_service.get_or_refresh_product.return_value = cache_result

        scanner = MonitoringScanner(
            product_cache_service=cache_service,
        )

        result = scanner.process_target(
            target=self.target,
            trigger="scanner",
        )

        self.assertFalse(result.success)
        self.assertIsNone(result.snapshot)
        self.assertEqual(
            result.error,
            "Monitoring target no longer exists.",
        )

    @patch(
        "app.api.v1.monitoring.services.scanner."
        "MonitoringScanner._update_schedule_metrics"
    )
    @patch(
        "app.api.v1.monitoring.services.scanner."
        "MonitoringScanner._get_due_targets"
    )
    @patch(
        "app.api.v1.monitoring.services.scanner."
        "MonitoringScanner._process_target"
    )
    def test_run_once_continues_after_one_target_crashes(
        self,
        mock_process_target,
        mock_get_due_targets,
        mock_update_schedule_metrics,
    ):
        first_target = SimpleNamespace(
            id=uuid4(),
            marketplace="wb",
        )
        second_target = SimpleNamespace(
            id=uuid4(),
            marketplace="ozon",
        )
        mock_get_due_targets.return_value = [
            first_target,
            second_target,
        ]
        mock_process_target.side_effect = [
            RuntimeError("unexpected target error"),
            MonitoringTargetProcessResult(success=True),
        ]

        scanner = MonitoringScanner()

        with patch(
            "app.api.v1.monitoring.services.scanner.logger.exception"
        ):
            processed_count = scanner.run_once()

        self.assertEqual(processed_count, 2)
        self.assertEqual(mock_process_target.call_count, 2)
        mock_update_schedule_metrics.assert_called_once_with()

    def test_temporary_gateway_unavailability_postpones_without_snapshot(self):
        cache_service = Mock()
        cache_service.get_or_refresh_product.side_effect = (
            MonitoringFetcherTemporarilyUnavailableError(
                "VPN parse window is not ready yet",
                retry_after_seconds=420,
            )
        )
        scanner = MonitoringScanner(
            product_cache_service=cache_service,
        )

        before = self.target.next_check_at
        result = scanner.process_target(
            target=self.target,
            trigger="scanner",
        )

        self.target.refresh_from_db()
        self.assertFalse(result.success)
        self.assertTrue(result.busy)
        self.assertEqual(ProductSnapshot.objects.count(), 0)
        self.assertGreater(self.target.next_check_at, before)
        self.assertIn(
            "VPN parse window is not ready yet",
            self.target.last_error,
        )
        self.assertNotEqual(self.target.status, "failed")

