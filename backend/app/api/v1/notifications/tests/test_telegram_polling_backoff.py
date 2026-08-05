from unittest.mock import MagicMock

from django.test import SimpleTestCase

from app.api.v1.notifications.telegram.polling import (
    TelegramPollingRunner,
)


class TelegramPollingBackoffTests(SimpleTestCase):
    def setUp(self):
        self.runner = TelegramPollingRunner(
            client=MagicMock(),
            router=MagicMock(),
            drop_pending_updates_on_start=False,
            error_sleep_seconds=2,
            max_error_sleep_seconds=60,
        )

    def test_uses_exponential_backoff(self):
        self.assertEqual(
            self.runner._get_error_sleep_seconds(
                consecutive_errors=1,
            ),
            2,
        )
        self.assertEqual(
            self.runner._get_error_sleep_seconds(
                consecutive_errors=2,
            ),
            4,
        )
        self.assertEqual(
            self.runner._get_error_sleep_seconds(
                consecutive_errors=5,
            ),
            32,
        )
        self.assertEqual(
            self.runner._get_error_sleep_seconds(
                consecutive_errors=6,
            ),
            60,
        )

    def test_rate_limit_retry_after_has_priority(self):
        self.assertEqual(
            self.runner._get_error_sleep_seconds(
                consecutive_errors=2,
                retry_after_seconds=25,
            ),
            25,
        )

    def test_rate_limit_delay_is_capped(self):
        self.assertEqual(
            self.runner._get_error_sleep_seconds(
                consecutive_errors=2,
                retry_after_seconds=120,
            ),
            60,
        )