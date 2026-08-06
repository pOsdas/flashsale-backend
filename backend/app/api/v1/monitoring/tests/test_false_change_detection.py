from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from app.api.v1.monitoring.models import AlertType
from app.api.v1.monitoring.services.change_detector import (
    detect_snapshot_changes,
)


class FalseChangeDetectionTests(SimpleTestCase):
    @staticmethod
    def _snapshot(
        *,
        snapshot_id: str,
        title: str = "Тестовый товар",
        price: Decimal = Decimal("1000.00"),
        rating: Decimal = Decimal("5.00"),
        reviews_count: int = 100,
        is_available: bool = True,
    ):
        target = SimpleNamespace(
            id="target-1",
            title=title,
            url="https://www.wildberries.ru/catalog/123/detail.aspx",
        )

        return SimpleNamespace(
            id=snapshot_id,
            target_id="target-1",
            target=target,
            title=title,
            price=price,
            rating=rating,
            reviews_count=reviews_count,
            is_available=is_available,
        )

    def test_does_not_create_rating_alert_when_rating_and_reviews_disappear(
        self,
    ) -> None:
        previous_snapshot = self._snapshot(
            snapshot_id="snapshot-1",
            rating=Decimal("5.00"),
            reviews_count=120,
        )
        current_snapshot = self._snapshot(
            snapshot_id="snapshot-2",
            rating=Decimal("0"),
            reviews_count=0,
        )

        candidates = detect_snapshot_changes(
            previous_snapshot=previous_snapshot,
            current_snapshot=current_snapshot,
        )

        rating_alerts = [
            candidate
            for candidate in candidates
            if candidate.alert_type == AlertType.RATING_CHANGED
        ]

        self.assertEqual(rating_alerts, [])

    def test_creates_real_rating_change_alert(self) -> None:
        previous_snapshot = self._snapshot(
            snapshot_id="snapshot-1",
            rating=Decimal("5.00"),
            reviews_count=120,
        )
        current_snapshot = self._snapshot(
            snapshot_id="snapshot-2",
            rating=Decimal("4.80"),
            reviews_count=121,
        )

        candidates = detect_snapshot_changes(
            previous_snapshot=previous_snapshot,
            current_snapshot=current_snapshot,
        )

        rating_alerts = [
            candidate
            for candidate in candidates
            if candidate.alert_type == AlertType.RATING_CHANGED
        ]

        self.assertEqual(len(rating_alerts), 1)
        self.assertEqual(
            rating_alerts[0].old_value,
            {"rating": "5.00"},
        )
        self.assertEqual(
            rating_alerts[0].new_value["rating"],
            "4.80",
        )

    def test_creates_real_availability_change_alert(self) -> None:
        previous_snapshot = self._snapshot(
            snapshot_id="snapshot-1",
            is_available=True,
        )
        current_snapshot = self._snapshot(
            snapshot_id="snapshot-2",
            is_available=False,
        )

        candidates = detect_snapshot_changes(
            previous_snapshot=previous_snapshot,
            current_snapshot=current_snapshot,
        )

        availability_alerts = [
            candidate
            for candidate in candidates
            if candidate.alert_type == AlertType.BECAME_UNAVAILABLE
        ]

        self.assertEqual(len(availability_alerts), 1)
        self.assertEqual(
            availability_alerts[0].old_value,
            {"is_available": True},
        )
        self.assertEqual(
            availability_alerts[0].new_value,
            {"is_available": False},
        )