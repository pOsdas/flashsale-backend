import unittest
from unittest.mock import patch

from ozon_browser_fetcher.app.models.product import Product
from ozon_browser_fetcher.app.parsers.product_parser import (
    is_ozon_short_url,
    parse_product_from_page,
)


class FakeBodyLocator:
    def inner_text(self, timeout: int) -> str:
        return "Тестовый товар 1 999 ₽"


class FakePage:
    def __init__(self, url: str) -> None:
        self.url = url

    def locator(self, selector: str):
        if selector == "body":
            return FakeBodyLocator()
        raise AssertionError(f"unexpected selector: {selector}")

    def title(self) -> str:
        return "Тестовый товар купить на Ozon"


class OzonShortProductURLTests(unittest.TestCase):
    def test_recognizes_ozon_short_url(self) -> None:
        self.assertTrue(
            is_ozon_short_url("https://ozon.ru/t/TOMpiEV")
        )
        self.assertTrue(
            is_ozon_short_url("https://www.ozon.ru/t/TOMpiEV/")
        )
        self.assertFalse(
            is_ozon_short_url("https://example.com/t/TOMpiEV")
        )

    def test_short_url_uses_resolved_product_url_and_id(self) -> None:
        short_url = "https://ozon.ru/t/TOMpiEV"
        resolved_url = (
            "https://www.ozon.ru/product/"
            "korobka-podarochnaya-lavanda-22-h-16-5-h-10-sm-560643681/"
        )
        page = FakePage(short_url)

        def fake_wait_page_loaded(current_page, requested_url: str) -> None:
            self.assertEqual(
                requested_url,
                "https://www.ozon.ru/t/TOMpiEV",
            )
            current_page.url = resolved_url

        extracted_product = Product(
            sku="560643681",
            title="Коробка подарочная Лаванда",
            price_cents=199900,
            available=1,
        )

        with (
            patch(
                "ozon_browser_fetcher.app.parsers.product_parser.wait_page_loaded",
                side_effect=fake_wait_page_loaded,
            ),
            patch(
                "ozon_browser_fetcher.app.parsers.product_parser.is_antibot_url",
                return_value=False,
            ),
            patch(
                "ozon_browser_fetcher.app.parsers.product_parser.is_antibot_page",
                return_value=False,
            ),
            patch(
                "ozon_browser_fetcher.app.parsers.product_parser.extract_product_from_json_ld",
                return_value=extracted_product,
            ) as extract_json_ld,
            patch(
                "ozon_browser_fetcher.app.parsers.product_parser.prepare_product_for_return",
                side_effect=lambda product, expected_sku, page: product,
            ),
        ):
            product = parse_product_from_page(page, short_url)

        extract_json_ld.assert_called_once_with(
            page=page,
            product_id="560643681",
        )
        self.assertEqual(product.sku, "560643681")
        self.assertEqual(product.url, resolved_url)
        self.assertEqual(
            product.product_path,
            "/product/korobka-podarochnaya-lavanda-22-h-16-5-h-10-sm-560643681/",
        )


if __name__ == "__main__":
    unittest.main()
