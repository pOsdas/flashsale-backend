from unittest import TestCase
from unittest.mock import Mock

from wb_browser_fetcher.app.browser import WBBrowser


class WBBrowserProductParsingTests(TestCase):
    def setUp(self) -> None:
        self.browser = object.__new__(WBBrowser)
        self.browser.page = Mock()

    def test_rejects_generic_wildberries_title(self) -> None:
        self.browser.page.locator.return_value.inner_text.return_value = (
            "Каталог Wildberries"
        )

        result = self.browser._extract_dom_product(
            nm_id="123456789",
            page_title=(
                "Купить Интернет-магазин Wildberries: "
                "широкий ассортимент товаров - скидки каждый день!"
            ),
            candidates={
                "headings": [],
                "prices": ["1 099 ₽"],
                "ratings": [],
                "brands": [],
                "sellers": [],
            },
        )

        self.assertIsNone(result)

    def test_rejects_product_without_price(self) -> None:
        self.browser.page.locator.return_value.inner_text.return_value = (
            "ИМПУЛЬС МАРКЕТ Коврик большой игровой"
        )

        result = self.browser._extract_dom_product(
            nm_id="123456789",
            page_title="ИМПУЛЬС МАРКЕТ Коврик большой игровой",
            candidates={
                "headings": [
                    "ИМПУЛЬС МАРКЕТ Коврик большой игровой",
                ],
                "prices": [],
                "ratings": ["5 · 120 оценок"],
                "brands": [],
                "sellers": [],
            },
        )

        self.assertIsNone(result)

    def test_uses_h1_instead_of_generic_page_title(self) -> None:
        self.browser.page.locator.return_value.inner_text.return_value = (
            "ИМПУЛЬС МАРКЕТ Коврик большой игровой\n"
            "В корзину"
        )

        result = self.browser._extract_dom_product(
            nm_id="123456789",
            page_title=(
                "Купить Интернет-магазин Wildberries: "
                "широкий ассортимент товаров - скидки каждый день!"
            ),
            candidates={
                "headings": [
                    "ИМПУЛЬС МАРКЕТ Коврик большой игровой",
                ],
                "prices": ["1 092 ₽"],
                "ratings": ["5 · 120 оценок"],
                "brands": [],
                "sellers": [],
            },
        )

        self.assertIsNotNone(result)
        self.assertEqual(
            result["name"],
            "ИМПУЛЬС МАРКЕТ Коврик большой игровой",
        )
        self.assertEqual(result["salePriceU"], 109_200)
        self.assertEqual(result["reviewRating"], 5.0)
        self.assertEqual(result["feedbacks"], 120)
        self.assertEqual(result["totalQuantity"], 1)

    def test_does_not_mark_product_unavailable_from_invalid_dom(self) -> None:
        self.browser.page.locator.return_value.inner_text.return_value = (
            "ИМПУЛЬС МАРКЕТ Коврик большой игровой"
        )

        result = self.browser._extract_dom_product(
            nm_id="123456789",
            page_title="ИМПУЛЬС МАРКЕТ Коврик большой игровой",
            candidates={
                "headings": [
                    "ИМПУЛЬС МАРКЕТ Коврик большой игровой",
                ],
                "prices": [],
                "ratings": [],
                "brands": [],
                "sellers": [],
            },
        )

        self.assertIsNone(result)