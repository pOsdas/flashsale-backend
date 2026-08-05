import httpx
from django.test import SimpleTestCase

from app.api.v1.notifications.telegram.client import (
    TelegramApiError,
    TelegramBotClient,
)


class TelegramBotClientErrorTests(SimpleTestCase):
    def test_rate_limit_error_contains_retry_after_without_token(self):
        request = httpx.Request(
            "GET",
            "https://api.telegram.org/bot-secret-token/getUpdates",
        )
        response = httpx.Response(
            429,
            request=request,
            json={
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests",
                "parameters": {
                    "retry_after": 17,
                },
            },
        )

        client = TelegramBotClient(
            token="secret-token",
            client=httpx.Client(),
        )

        with self.assertRaises(TelegramApiError) as context:
            client._extract_result(
                response=response,
                method_name="getUpdates",
            )

        error = context.exception

        self.assertEqual(error.status_code, 429)
        self.assertEqual(error.retry_after_seconds, 17)
        self.assertNotIn("secret-token", str(error))
        self.assertNotIn(
            "api.telegram.org",
            str(error),
        )

    def test_server_error_does_not_include_request_url(self):
        request = httpx.Request(
            "GET",
            "https://api.telegram.org/bot-secret-token/getUpdates",
        )
        response = httpx.Response(
            502,
            request=request,
            text="Bad Gateway",
        )

        client = TelegramBotClient(
            token="secret-token",
            client=httpx.Client(),
        )

        with self.assertRaises(TelegramApiError) as context:
            client._extract_result(
                response=response,
                method_name="getUpdates",
            )

        error = context.exception

        self.assertEqual(error.status_code, 502)
        self.assertNotIn("secret-token", str(error))