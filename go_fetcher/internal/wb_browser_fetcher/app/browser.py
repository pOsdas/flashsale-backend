import threading
import time
import re
import json
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, quote_plus, urlparse

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright


def parse_cookie_header(cookie_header: str) -> List[Dict]:
    cookies: List[Dict] = []

    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue

        name, value = part.split("=", 1)
        name = name.strip()
        if not name:
            continue

        cookies.append(
            {
                "name": name,
                "value": value.strip(),
                "domain": ".wildberries.ru",
                "path": "/",
                "secure": True,
            }
        )

    return cookies


class WBBrowser:
    def __init__(self, cookie_path: str) -> None:
        self.cookie_path = Path(cookie_path)
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.cookie_mtime_ns = -1
        self.lock = threading.Lock()

    def start(self) -> None:
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=True)
        self.context = self.browser.new_context(
            viewport={"width": 1400, "height": 900},
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )
        self._reload_cookies(force=True)
        self.page = self.context.new_page()
        self.page.set_default_timeout(30_000)
        self.page.set_default_navigation_timeout(30_000)
        response = self.page.goto(
            "https://www.wildberries.ru/",
            wait_until="domcontentloaded",
        )
        print(
            "WB browser started: "
            f"homepage_status={response.status if response else 0}, "
            f"url={self.page.url}, "
            f"cookies={self._cookie_names()}",
            flush=True,
        )

    def _reload_cookies(self, force: bool = False) -> None:
        if self.context is None:
            raise RuntimeError("browser context is not started")

        stat = self.cookie_path.stat()
        if not force and stat.st_mtime_ns == self.cookie_mtime_ns:
            return

        cookie_header = self.cookie_path.read_text(encoding="utf-8").strip()
        cookies = parse_cookie_header(cookie_header)
        if not cookies:
            raise RuntimeError("WB cookie file is empty or invalid")

        self.context.clear_cookies()
        self.context.add_cookies(cookies)
        self.cookie_mtime_ns = stat.st_mtime_ns
        print(
            "WB cookies loaded from file: "
            f"count={len(cookies)}, "
            f"names={sorted(cookie['name'] for cookie in cookies)}",
            flush=True,
        )

    def _cookie_names(self) -> List[str]:
        if self.context is None:
            return []
        return sorted(cookie["name"] for cookie in self.context.cookies())

    def _fetch_once(self, url: str) -> Dict:
        if self.page is None:
            raise RuntimeError("WB browser page is not ready")

        return self.page.evaluate(
            r"""
            async (url) => {
                const response = await fetch(url, {
                    method: "GET",
                    credentials: "include",
                    headers: {"Accept": "application/json, text/plain, */*"}
                });
                return {
                    status_code: response.status,
                    body: await response.text()
                };
            }
            """,
            url,
        )

    @staticmethod
    def _referer_for_api_url(url: str) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)

        nm_values = query.get("nm") or []
        if ("/card/" in parsed.path or "/u-card/" in parsed.path) and nm_values:
            nm_id = str(nm_values[0]).strip()
            if nm_id.isdigit():
                return f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"

        search_values = query.get("query") or []
        if "/search/" in parsed.path and search_values:
            search_query = str(search_values[0]).strip()
            if search_query and not search_query.startswith("menu_redirect_subject_v2_"):
                return (
                    "https://www.wildberries.ru/catalog/0/search.aspx?search="
                    f"{quote_plus(search_query)}"
                )

        return "https://www.wildberries.ru/"

    def _prepare_page_for_request(self, url: str) -> Optional[Dict]:
        if self.page is None:
            raise RuntimeError("WB browser page is not ready")

        referer = self._referer_for_api_url(url)
        original_query = parse_qs(urlparse(url).query)
        target_nm = str((original_query.get("nm") or [""])[0]).strip()
        captured_responses: List[Dict] = []

        def capture_response(response) -> None:
            if "/__internal/u-card/cards/v4/detail" not in response.url:
                return

            response_nm = str(
                (parse_qs(urlparse(response.url).query).get("nm") or [""])[0]
            )
            if target_nm not in response_nm.split(";"):
                return

            print(
                "WB browser observed frontend API response: "
                f"status={response.status}, url={response.url}",
                flush=True,
            )
            if not target_nm or response.status < 200 or response.status >= 300:
                return

            try:
                captured_responses.append(
                    {
                        "status_code": response.status,
                        "body": response.text(),
                    }
                )
            except Exception as exc:
                print(
                    "WB browser response capture failed: "
                    f"url={response.url}, error={exc}",
                    flush=True,
                )

        self.page.on("response", capture_response)
        response = self.page.goto(referer, wait_until="domcontentloaded")
        time.sleep(2)
        self.page.remove_listener("response", capture_response)
        title = self.page.title()
        dom_candidates: Dict[str, List[str]] = {}
        for name, selector in {
            "headings": "h1",
            "prices": '[class*="price"]',
            "sellers": 'a[href*="seller"], [class*="seller"]',
            "ratings": '[class*="rating"], [class*="review"]',
            "brands": 'a[href*="brand"], [class*="brand"]',
        }.items():
            try:
                dom_candidates[name] = [
                    " ".join(value.split())[:200]
                    for value in self.page.locator(selector).all_inner_texts()
                    if value.strip()
                ][:20]
            except Exception:
                dom_candidates[name] = []
        print(
            "WB browser request page prepared: "
            f"status={response.status if response else 0}, "
            f"url={self.page.url}, title={title!r}",
            flush=True,
        )

        if captured_responses:
            captured = captured_responses[-1]
            print(
                "WB browser captured frontend API response: "
                f"status={captured['status_code']}, nm={target_nm}",
                flush=True,
            )
            return captured

        if target_nm:
            product = self._extract_dom_product(
                nm_id=target_nm,
                page_title=title,
                candidates=dom_candidates,
            )
            if product is not None:
                print(
                    "WB browser product extracted from DOM: "
                    f"nm={target_nm}, title={product['name']!r}, "
                    f"price_u={product['salePriceU']}",
                    flush=True,
                )
                return {
                    "status_code": 200,
                    "body": '{"products":[' + json.dumps(
                        product,
                        ensure_ascii=False,
                    ) + "]}",
                }

        search_query = str((original_query.get("query") or [""])[0]).strip()
        if "/search/" in urlparse(url).path and search_query:
            products = self._extract_search_products()
            if products:
                print(
                    "WB browser search products extracted from DOM: "
                    f"query={search_query!r}, products={len(products)}",
                    flush=True,
                )
                return {
                    "status_code": 200,
                    "body": json.dumps(
                        {"products": products},
                        ensure_ascii=False,
                    ),
                }

        return None

    def _extract_search_products(self) -> List[Dict]:
        if self.page is None:
            return []

        return self.page.evaluate(
            r"""
            () => {
                const products = new Map();
                const anchors = document.querySelectorAll(
                    'a[href*="/catalog/"][href*="/detail.aspx"]'
                );

                for (const anchor of anchors) {
                    const match = anchor.href.match(/\/catalog\/(\d+)\/detail\.aspx/);
                    if (!match || products.has(match[1])) {
                        continue;
                    }

                    const card = anchor.closest('article')
                        || anchor.closest('[class*="product-card"]')
                        || anchor.closest('li')
                        || anchor.parentElement;
                    if (!card) {
                        continue;
                    }

                    const image = card.querySelector('img[alt]');
                    const titleElement = card.querySelector(
                        '[class*="name"], [class*="title"]'
                    );
                    const name = (
                        anchor.getAttribute('aria-label')
                        || anchor.getAttribute('title')
                        || (image && image.getAttribute('alt'))
                        || (titleElement && titleElement.textContent)
                        || ''
                    ).trim();
                    if (!name) {
                        continue;
                    }

                    const text = (card.innerText || '').replace(/\u00a0/g, ' ');
                    const amounts = [...text.matchAll(/([\d\s]+)\s*₽/g)]
                        .map(item => Number(item[1].replace(/\D/g, '')) * 100)
                        .filter(value => Number.isFinite(value) && value > 0);
                    const ratingMatch = text.match(/(\d(?:[,.]\d)?)\s*[·•]?\s*(\d+)\s+(?:оцен|отзыв)/i);

                    products.set(match[1], {
                        id: Number(match[1]),
                        brand: '',
                        supplier: '',
                        reviewRating: ratingMatch
                            ? Number(ratingMatch[1].replace(',', '.'))
                            : 0,
                        feedbacks: ratingMatch ? Number(ratingMatch[2]) : 0,
                        name: name,
                        priceU: amounts.length > 1 ? amounts[1] : (amounts[0] || 0),
                        salePriceU: amounts[0] || 0,
                        totalQuantity: 1,
                        sizes: []
                    });
                }

                return [...products.values()];
            }
            """
        )

    def _extract_dom_product(
        self,
        *,
        nm_id: str,
        page_title: str,
        candidates: Dict[str, List[str]],
    ) -> Optional[Dict]:
        if self.page is None:
            return None

        brands = [
            value
            for value in candidates.get("brands", [])
            if value not in {"Бренды", "Купить сейчас", "В корзину", "В избранное"}
            and "каталог бренда" not in value.lower()
            and len(value) <= 80
        ]
        brand_counts = Counter(brands)
        brand = next(
            (value for value in brands if brand_counts[value] >= 2),
            brands[0] if brands else "",
        )

        sellers = [
            value
            for value in candidates.get("sellers", [])
            if value not in {"Стать продавцом", "Продавать товары", "Находки из Китая", "РИВ ГОШ"}
            and not re.search(r"\d[,.]\d", value)
            and len(value.split()) <= 5
        ]
        seller = sellers[0] if sellers else ""

        name = page_title
        title_suffix = re.compile(
            rf"\s+{re.escape(brand)}\s+{re.escape(nm_id)}\s+купить.*$",
            re.IGNORECASE,
        ) if brand else re.compile(
            rf"\s+{re.escape(nm_id)}\s+купить.*$",
            re.IGNORECASE,
        )
        name = title_suffix.sub("", name).strip()

        price_values: List[int] = []
        for value in candidates.get("prices", []):
            amounts = re.findall(r"([\d\s\u00a0]+)\s*₽", value)
            if not amounts:
                continue
            price_values = [
                int(re.sub(r"\D", "", amount)) * 100
                for amount in amounts
                if re.sub(r"\D", "", amount)
            ]
            if price_values:
                break

        rating = 0.0
        feedbacks = 0
        for value in candidates.get("ratings", []):
            match = re.search(r"(\d(?:[,.]\d)?)\s*·\s*(\d+)\s+оцен", value)
            if match:
                rating = float(match.group(1).replace(",", "."))
                feedbacks = int(match.group(2))
                break

        body_text = self.page.locator("body").inner_text()
        available = "В корзину" in body_text or "Купить сейчас" in body_text

        if not name or not nm_id.isdigit():
            return None

        current_price = price_values[0] if price_values else 0
        old_price = price_values[1] if len(price_values) > 1 else current_price
        return {
            "id": int(nm_id),
            "brand": brand,
            "supplier": seller,
            "reviewRating": rating,
            "feedbacks": feedbacks,
            "name": name,
            "priceU": old_price,
            "salePriceU": current_price,
            "totalQuantity": 1 if available else 0,
            "sizes": [],
        }

    def _recover_antibot_session(self) -> None:
        if self.context is None or self.page is None:
            raise RuntimeError("WB browser is not ready")

        cookies = [
            cookie
            for cookie in self.context.cookies()
            if cookie.get("name") != "x_wbaas_token"
        ]
        self.context.clear_cookies()
        if cookies:
            self.context.add_cookies(cookies)

        print(
            "WB browser session recovery started: "
            "removed_cookie=x_wbaas_token",
            flush=True,
        )
        response = self.page.goto(
            "https://www.wildberries.ru/",
            wait_until="domcontentloaded",
        )
        time.sleep(5)
        print(
            "WB browser session recovery finished: "
            f"homepage_status={response.status if response else 0}, "
            f"url={self.page.url}, "
            f"cookies={self._cookie_names()}",
            flush=True,
        )

    def fetch(self, url: str) -> Dict:
        with self.lock:
            if self.page is None or self.context is None:
                raise RuntimeError("WB browser is not ready")

            self._reload_cookies()
            captured_result = self._prepare_page_for_request(url)
            if captured_result is not None:
                return captured_result

            result = self._fetch_once(url)
            status_code = int(result.get("status_code") or 0)
            print(
                "WB browser fetch completed: "
                f"status={status_code}, url={url}",
                flush=True,
            )

            if status_code in {403, 498}:
                self._recover_antibot_session()
                result = self._fetch_once(url)
                print(
                    "WB browser fetch after recovery completed: "
                    f"status={int(result.get('status_code') or 0)}, "
                    f"url={url}",
                    flush=True,
                )

            return result

    def is_ready(self) -> bool:
        return bool(self.browser and self.browser.is_connected() and self.page)

    def stop(self) -> None:
        if self.context is not None:
            self.context.close()
        if self.browser is not None:
            self.browser.close()
        if self.playwright is not None:
            self.playwright.stop()
