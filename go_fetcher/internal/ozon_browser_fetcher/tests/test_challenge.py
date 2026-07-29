import unittest
from unittest.mock import patch

from ozon_browser_fetcher.app.browser.challenge import wait_for_ozon_page_ready
from ozon_browser_fetcher.app.browser.errors import OzonAntibotRejectedError


class FakeButton:
    def __init__(self, page) -> None:
        self.page = page
        self.first = self

    def count(self) -> int:
        return 1

    def click(self, timeout: int) -> None:
        self.page.refreshed = True
        self.page.state = {
            "title": "Тестовый товар купить на Ozon",
            "ready_state": "complete",
            "body_length": 1200,
            "body_prefix": "Тестовый товар 1 999 ₽",
        }
        self.page.url = "https://www.ozon.ru/product/test-123/?abt_att=1"


class FakePage:
    def __init__(self, state: dict, url: str) -> None:
        self.state = state
        self.url = url
        self.refreshed = False

    def evaluate(self, script: str) -> dict:
        return dict(self.state)

    def get_by_role(self, role: str, name):
        return FakeButton(self)

    def reload(self, wait_until: str, timeout: int) -> None:
        self.refreshed = True

    def wait_for_timeout(self, timeout: int) -> None:
        return None


class ChallengeTests(unittest.TestCase):
    def test_normal_page_is_ready_immediately(self) -> None:
        page = FakePage(
            state={
                "title": "Тестовый товар купить на Ozon",
                "ready_state": "complete",
                "body_length": 1200,
                "body_prefix": "Тестовый товар 1 999 ₽",
            },
            url="https://www.ozon.ru/product/test-123/",
        )

        result = wait_for_ozon_page_ready(
            page,
            timeout_ms=50,
            refresh_attempts=0,
        )

        self.assertEqual(result["title"], "Тестовый товар купить на Ozon")
        self.assertFalse(result["challenge_seen"])

    def test_antibot_captcha_uses_one_refresh_and_then_passes(self) -> None:
        page = FakePage(
            state={
                "title": "Antibot Captcha",
                "ready_state": "complete",
                "body_length": 75,
                "body_prefix": (
                    "Oops, something went wrong. Please refresh the page"
                ),
            },
            url="https://www.ozon.ru/product/test-123/?__rr=1",
        )

        with patch(
            "ozon_browser_fetcher.app.browser.challenge.time.sleep",
            return_value=None,
        ):
            result = wait_for_ozon_page_ready(
                page,
                timeout_ms=1,
                refresh_attempts=1,
            )

        self.assertTrue(page.refreshed)
        self.assertEqual(result["title"], "Тестовый товар купить на Ozon")

    def test_persistent_antibot_captcha_is_rejected(self) -> None:
        page = FakePage(
            state={
                "title": "Antibot Captcha",
                "ready_state": "complete",
                "body_length": 75,
                "body_prefix": (
                    "Oops, something went wrong. Please refresh the page"
                ),
            },
            url="https://www.ozon.ru/product/test-123/?__rr=1",
        )

        class NonPassingButton(FakeButton):
            def click(self, timeout: int) -> None:
                self.page.refreshed = True

        page.get_by_role = lambda role, name: NonPassingButton(page)

        with patch(
            "ozon_browser_fetcher.app.browser.challenge.time.sleep",
            return_value=None,
        ):
            with self.assertRaises(OzonAntibotRejectedError):
                wait_for_ozon_page_ready(
                    page,
                    timeout_ms=1,
                    refresh_attempts=1,
                )

        self.assertTrue(page.refreshed)


if __name__ == "__main__":
    unittest.main()
