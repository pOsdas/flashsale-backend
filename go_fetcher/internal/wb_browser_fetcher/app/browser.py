import json
import os
import re
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, quote_plus, urlparse

from playwright.sync_api import BrowserContext, Page

from browser_runtime.chrome_cdp import ChromeCDPConfiguration, ChromeCDPSession


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
        self.runtime = ChromeCDPSession(
            ChromeCDPConfiguration.from_environment("wb")
        )
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.cookie_mtime_ns = -1
        self.lock = threading.Lock()
        self.proxy_url = ""
        self.session_id = ""
        self.profile_id = ""
        self.request_timeout_ms = self._env_int(
            "WB_BROWSER_REQUEST_TIMEOUT_MS",
            60_000,
        )
        self.ready_timeout_ms = self._env_int(
            "WB_BROWSER_READY_TIMEOUT_MS",
            25_000,
        )
        self.ready_poll_interval_ms = self._env_int(
            "WB_BROWSER_READY_POLL_INTERVAL_MS",
            500,
        )
        self.post_ready_delay_ms = self._env_int(
            "WB_BROWSER_POST_READY_DELAY_MS",
            2_000,
        )
        self.recovery_delay_ms = self._env_int(
            "WB_BROWSER_RECOVERY_DELAY_MS",
            6_000,
        )
        self.diagnostics_dir = Path(
            os.getenv(
                "WB_BROWSER_DIAGNOSTICS_DIR",
                "/tmp/wb-browser-diagnostics",
            )
        )

    def start(
        self,
        proxy_url: str = "",
        session_id: str = "",
        profile_id: str = "",
    ) -> None:
        self.proxy_url = proxy_url.strip()
        self.session_id = session_id.strip()
        self.profile_id = profile_id.strip()

        try:
            self.context = self.runtime.start(
                proxy_url=self.proxy_url,
                session_id=self.session_id,
                profile_id=self.profile_id,
            )
            self._reload_cookies(force=True)

            existing_pages = self.context.pages
            self.page = (
                existing_pages[0]
                if existing_pages
                else self.context.new_page()
            )
            self.page.set_default_timeout(self.request_timeout_ms)
            self.page.set_default_navigation_timeout(self.request_timeout_ms)

            response = self.page.goto(
                "https://www.wildberries.ru/",
                wait_until="domcontentloaded",
            )
            self._wait_for_page_ready(
                stage="startup_homepage",
                require_search_content=False,
            )
            print(
                "WB Google Chrome connected over CDP: "
                f"cdp_url={self.runtime.config.cdp_url}, "
                f"proxy_enabled={bool(self.proxy_url)}, "
                f"session={self.session_id or 'direct'}, "
                f"profile_id={self.profile_id or self.session_id or 'direct'}, "
                f"profile_dir={self.runtime.profile_dir}, "
                f"homepage_status={response.status if response else 0}, "
                f"url={self.page.url}, "
                f"cookies={self._cookie_names()}",
                flush=True,
            )
        except Exception:
            self._stop_locked()
            raise

    def configuration_ready(self) -> bool:
        if not self._env_bool("WB_BROWSER_IMPORT_COOKIE_FILE", True):
            return True
        try:
            cookie_header = self.cookie_path.read_text(
                encoding="utf-8"
            ).strip()
        except OSError:
            return False
        return bool(parse_cookie_header(cookie_header))

    def _reload_cookies(self, force: bool = False) -> None:
        if self.context is None:
            raise RuntimeError("browser context is not started")
        if not self._env_bool("WB_BROWSER_IMPORT_COOKIE_FILE", True):
            return

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
                    body: await response.text(),
                    headers: Object.fromEntries(response.headers.entries()),
                    response_url: response.url
                };
            }
            """,
            url,
        )

    def _wait_for_page_ready(
        self,
        *,
        stage: str,
        require_search_content: bool,
    ) -> bool:
        if self.page is None or self.context is None:
            raise RuntimeError("WB browser page is not ready")

        deadline = time.monotonic() + (self.ready_timeout_ms / 1000)
        last_cookie_signature: tuple[str, ...] | None = None
        stable_cookie_checks = 0
        last_state: Dict = {}

        while time.monotonic() < deadline:
            try:
                title = self.page.title().strip()
            except Exception:
                title = ""

            try:
                state = self.page.evaluate(
                    r"""
                    () => {
                        const body = document.body;
                        const bodyText = body ? (body.innerText || '') : '';
                        const productLinks = document.querySelectorAll(
                            'a[href*="/catalog/"][href*="/detail.aspx"]'
                        ).length;
                        const blocked = /403 forbidden|access denied|captcha|проверяем ваш браузер|что-то пошло не так/i.test(
                            bodyText
                        );
                        return {
                            body_length: bodyText.length,
                            product_links: productLinks,
                            blocked: blocked,
                            ready_state: document.readyState
                        };
                    }
                    """
                )
            except Exception:
                state = {
                    "body_length": 0,
                    "product_links": 0,
                    "blocked": False,
                    "ready_state": "",
                }

            cookie_names = tuple(self._cookie_names())
            if cookie_names == last_cookie_signature:
                stable_cookie_checks += 1
            else:
                stable_cookie_checks = 0
                last_cookie_signature = cookie_names

            title_ready = bool(
                title
                and title != "..."
                and "403 forbidden" not in title.lower()
                and "captcha" not in title.lower()
            )
            body_ready = (
                int(state.get("body_length") or 0) >= 500
                and not bool(state.get("blocked"))
            )
            search_ready = (
                not require_search_content
                or int(state.get("product_links") or 0) > 0
            )
            cookies_stable = stable_cookie_checks >= 2

            last_state = {
                "title": title,
                "url": self.page.url,
                "body_length": int(state.get("body_length") or 0),
                "product_links": int(state.get("product_links") or 0),
                "blocked": bool(state.get("blocked")),
                "ready_state": str(state.get("ready_state") or ""),
                "cookies": list(cookie_names),
                "cookies_stable": cookies_stable,
            }

            if title_ready and body_ready and search_ready and cookies_stable:
                if self.post_ready_delay_ms > 0:
                    self.page.wait_for_timeout(self.post_ready_delay_ms)
                print(
                    "WB browser page ready: "
                    f"stage={stage}, url={self.page.url}, "
                    f"title={title!r}, "
                    f"body_length={last_state['body_length']}, "
                    f"product_links={last_state['product_links']}, "
                    f"cookies={last_state['cookies']}",
                    flush=True,
                )
                return True

            self.page.wait_for_timeout(self.ready_poll_interval_ms)

        print(
            "WB browser page readiness timeout: "
            f"stage={stage}, state={json.dumps(last_state, ensure_ascii=False)}",
            flush=True,
        )
        return False

    def _save_diagnostics(
        self,
        *,
        stage: str,
        request_url: str,
        result: Optional[Dict] = None,
        error: str = "",
    ) -> None:
        if self.page is None:
            return

        try:
            self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            safe_stage = re.sub(r"[^a-zA-Z0-9_-]+", "-", stage).strip("-")
            prefix = self.diagnostics_dir / f"{timestamp}-{safe_stage}"

            try:
                title = self.page.title()
            except Exception:
                title = ""

            try:
                body_text = self.page.locator("body").inner_text(timeout=5_000)
            except Exception as exc:
                body_text = f"Could not read body: {exc}"

            try:
                html = self.page.content()
            except Exception as exc:
                html = f"Could not read HTML: {exc}"

            cookies = []
            if self.context is not None:
                for cookie in self.context.cookies():
                    cookies.append(
                        {
                            "name": cookie.get("name"),
                            "domain": cookie.get("domain"),
                            "path": cookie.get("path"),
                            "expires": cookie.get("expires"),
                            "httpOnly": cookie.get("httpOnly"),
                            "secure": cookie.get("secure"),
                            "sameSite": cookie.get("sameSite"),
                        }
                    )

            try:
                storage = self.page.evaluate(
                    r"""
                    () => ({
                        local_storage_keys: Object.keys(localStorage),
                        session_storage_keys: Object.keys(sessionStorage)
                    })
                    """
                )
            except Exception as exc:
                storage = {"error": str(exc)}

            metadata = {
                "stage": stage,
                "request_url": request_url,
                "page_url": self.page.url,
                "title": title,
                "profile_id": self.profile_id,
                "session_id": self.session_id,
                "proxy_enabled": bool(self.proxy_url),
                "cookies": cookies,
                "storage": storage,
                "result": result or {},
                "error": error,
            }

            prefix.with_suffix(".json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            prefix.with_suffix(".html").write_text(
                html,
                encoding="utf-8",
            )
            prefix.with_suffix(".txt").write_text(
                "\n".join(
                    [
                        f"stage={stage}",
                        f"request_url={request_url}",
                        f"page_url={self.page.url}",
                        f"title={title}",
                        f"error={error}",
                        "",
                        body_text,
                    ]
                ),
                encoding="utf-8",
            )
            try:
                self.page.screenshot(
                    path=str(prefix.with_suffix(".png")),
                    full_page=True,
                )
            except Exception as exc:
                print(
                    "WB browser diagnostic screenshot failed: "
                    f"stage={stage}, error={exc}",
                    flush=True,
                )

            print(
                "WB browser diagnostics saved: "
                f"stage={stage}, prefix={prefix}",
                flush=True,
            )
        except Exception as exc:
            print(
                "WB browser diagnostics failed: "
                f"stage={stage}, error={exc}",
                flush=True,
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
        self._wait_for_page_ready(
            stage="request_page",
            require_search_content=(
                "/search/" in urlparse(url).path
                and bool(str((original_query.get("query") or [""])[0]).strip())
            ),
        )
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
        self._wait_for_page_ready(
            stage="recovery_homepage",
            require_search_content=False,
        )
        if self.recovery_delay_ms > 0:
            self.page.wait_for_timeout(self.recovery_delay_ms)
        print(
            "WB browser session recovery finished: "
            f"homepage_status={response.status if response else 0}, "
            f"url={self.page.url}, "
            f"cookies={self._cookie_names()}",
            flush=True,
        )

    def fetch(
        self,
        url: str,
        proxy_url: str = "",
        vpn_session_id: str = "",
        browser_profile_id: str = "",
    ) -> Dict:
        with self.lock:
            self._ensure_session_locked(
                proxy_url=proxy_url,
                session_id=vpn_session_id,
                profile_id=browser_profile_id,
            )
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
                self._save_diagnostics(
                    stage="before_recovery",
                    request_url=url,
                    result=result,
                )
                self._recover_antibot_session()
                captured_result = self._prepare_page_for_request(url)
                if captured_result is not None:
                    return captured_result

                result = self._fetch_once(url)
                recovered_status = int(result.get("status_code") or 0)
                print(
                    "WB browser fetch after recovery completed: "
                    f"status={recovered_status}, "
                    f"url={url}",
                    flush=True,
                )

                if recovered_status in {403, 498}:
                    self._save_diagnostics(
                        stage="after_recovery_rejected",
                        request_url=url,
                        result=result,
                    )

            return result

    def _ensure_session_locked(
        self,
        *,
        proxy_url: str,
        session_id: str,
        profile_id: str,
    ) -> None:
        normalized_proxy = proxy_url.strip()
        normalized_session = session_id.strip()
        normalized_profile = profile_id.strip()
        if (
            self.is_ready()
            and self.proxy_url == normalized_proxy
            and self.session_id == normalized_session
            and self.profile_id == normalized_profile
        ):
            return
        self._stop_locked()
        self.start(
            proxy_url=normalized_proxy,
            session_id=normalized_session,
            profile_id=normalized_profile,
        )

    def is_ready(self) -> bool:
        return bool(
            self.runtime.is_ready()
            and self.context is not None
            and self.page is not None
            and not self.page.is_closed()
        )

    def stop(self) -> None:
        with self.lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        if self.page is not None:
            try:
                self.page.close()
            except Exception:
                pass
            finally:
                self.page = None
        self.context = None
        self.runtime.stop()
        self.proxy_url = ""
        self.session_id = ""
        self.profile_id = ""

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw_value = os.getenv(name)
        if raw_value is None or not raw_value.strip():
            return default
        return raw_value.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        raw_value = os.getenv(name)
        if raw_value is None or not raw_value.strip():
            return default
        try:
            return max(1, int(raw_value))
        except ValueError:
            return default
