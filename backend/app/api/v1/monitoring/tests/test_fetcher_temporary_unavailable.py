from unittest.mock import Mock, patch

import httpx
from django.test import SimpleTestCase

from app.api.v1.monitoring.services.fetcher_client import (
    HttpMonitoringFetcherClient,
    MonitoringFetcherTemporarilyUnavailableError,
)


class HttpMonitoringFetcherTemporaryUnavailableTests(SimpleTestCase):
    @patch("app.api.v1.monitoring.services.fetcher_client.httpx.Client")
    def test_503_is_exposed_as_temporary_unavailability(
        self,
        mock_client_class,
    ):
        request = httpx.Request(
            "POST",
            "http://go_fetcher:8090/api/v1/fetch/product/",
        )
        response = httpx.Response(
            status_code=503,
            headers={"Retry-After": "417"},
            json={
                "status": "error",
                "error": "VPN parse window is not ready yet",
            },
            request=request,
        )

        client_context = Mock()
        client_context.__enter__ = Mock(return_value=client_context)
        client_context.__exit__ = Mock(return_value=False)
        client_context.post.return_value = response
        mock_client_class.return_value = client_context

        client = HttpMonitoringFetcherClient(
            base_url="http://go_fetcher:8090",
            product_endpoint="/api/v1/fetch/product/",
            api_key="test-key",
            timeout_seconds=450,
        )

        with self.assertRaises(
            MonitoringFetcherTemporarilyUnavailableError
        ) as raised:
            client.fetch_product(
                marketplace="ozon",
                url="https://www.ozon.ru/product/123/",
                external_id="123",
            )

        self.assertEqual(raised.exception.retry_after_seconds, 417)
        self.assertIn(
            "VPN parse window is not ready yet",
            str(raised.exception),
        )
