from datetime import datetime
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from app.api.v1.notifications.services.notification_builder import (
    AlertNotificationBuilder,
)


class AlertNotificationBuilderValueTests(SimpleTestCase):
    def _alert(
        self,
        *,
        alert_type: str,
        old_value,
        new_value,
        snapshot_title: str = "Тестовый товар",
    ):
        return SimpleNamespace(
            alert_type=alert_type,
            old_value=old_value,
            new_value=new_value,
            created_at=timezone.make_aware(
                datetime(2026, 8, 5, 16, 17),
            ),
            snapshot=SimpleNamespace(
                title=snapshot_title,
                price=None,
                rating=None,
                reviews_count=None,
                is_available=None,
            ),
            target=SimpleNamespace(
                title=snapshot_title,
                marketplace="wb",
            ),
        )

    def test_formats_price_dictionary(self) -> None:
        alert = self._alert(
            alert_type="price_dropped",
            old_value={
                "price": "54354.00",
            },
            new_value={
                "price": "1092.00",
                "percent_change": "-97.99",
            },
        )

        message = AlertNotificationBuilder.build_telegram_message(alert)

        self.assertIn("Было:\n54 354 ₽", message)
        self.assertIn("Стало:\n1 092 ₽ (-97.99%)", message)
        self.assertNotIn("{'price':", message)

    def test_formats_title_dictionary(self) -> None:
        alert = self._alert(
            alert_type="title_changed",
            old_value={
                "title": "Старое название",
            },
            new_value={
                "title": "Новое название",
            },
            snapshot_title="Новое название",
        )

        message = AlertNotificationBuilder.build_telegram_message(alert)

        self.assertIn("Было:\nСтарое название", message)
        self.assertIn("Стало:\nНовое название", message)
        self.assertNotIn("{'title':", message)

    def test_formats_rating_dictionary(self) -> None:
        alert = self._alert(
            alert_type="rating_changed",
            old_value={
                "rating": "5.00",
            },
            new_value={
                "rating": "4.80",
                "percent_change": "-4.00",
            },
        )

        message = AlertNotificationBuilder.build_telegram_message(alert)

        self.assertIn("Было:\n5", message)
        self.assertIn("Стало:\n4.8", message)
        self.assertNotIn("{'rating':", message)

    def test_formats_availability_dictionary(self) -> None:
        alert = self._alert(
            alert_type="became_unavailable",
            old_value={
                "is_available": True,
            },
            new_value={
                "is_available": False,
            },
        )

        message = AlertNotificationBuilder.build_telegram_message(alert)

        self.assertIn("Было:\nВ наличии", message)
        self.assertIn("Стало:\nНет в наличии", message)

    def test_formats_reviews_count_dictionary(self) -> None:
        alert = self._alert(
            alert_type="reviews_count_changed",
            old_value={
                "reviews_count": 999,
            },
            new_value={
                "reviews_count": 1000,
                "percent_change": "0.10",
            },
        )

        message = AlertNotificationBuilder.build_telegram_message(alert)

        self.assertIn("Было:\n999", message)
        self.assertIn("Стало:\n1 000", message)