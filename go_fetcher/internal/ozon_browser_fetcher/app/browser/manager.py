import os
import time
from typing import Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Request,
    Route,
    sync_playwright,
)

from ozon_browser_fetcher.app.browser.cookie_loader import (
    extract_cookie_names,
    load_cookie_header,
    parse_cookie_header,
)
from ozon_browser_fetcher.app.metrics import (
    OZON_BROWSER_LAST_SUCCESSFUL_START_TIMESTAMP_SECONDS,
    OZON_BROWSER_LIFECYCLE_TOTAL,
    OZON_BROWSER_PAGES_ACTIVE,
    OZON_BROWSER_PAGE_EVENTS_TOTAL,
    OZON_BROWSER_START_DURATION_SECONDS,
    OZON_BROWSER_WORKER_READY,
)


class BrowserManager:
    def __init__(self) -> None:
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None

    def start(self, cookie_path: str) -> None:
        if self.is_ready():
            return

        started_at = time.monotonic()
        headless = (
            os.getenv(
                "OZON_BROWSER_HEADLESS",
                "true",
            ).lower()
            == "true"
        )

        try:
            self.playwright = sync_playwright().start()

            self.browser = self.playwright.chromium.launch(
                headless=headless,
            )

            self.context = self.browser.new_context(
                viewport={
                    "width": 1400,
                    "height": 900,
                },
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                service_workers="block",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36 "
                    "Edg/125.0.0.0"
                ),
                extra_http_headers={
                    "Accept-Language": (
                        "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
                    ),
                },
            )

            self.context.route(
                "**/*",
                self._route_handler,
            )

            cookie_header = load_cookie_header(cookie_path)
            cookies = parse_cookie_header(cookie_header)

            if cookies:
                self.context.add_cookies(cookies)

            OZON_BROWSER_WORKER_READY.set(1)
            OZON_BROWSER_LIFECYCLE_TOTAL.labels(
                event="start_success",
            ).inc()
            OZON_BROWSER_LAST_SUCCESSFUL_START_TIMESTAMP_SECONDS.set(
                time.time()
            )

            print(
                f"Ozon browser started. Headless: {headless}"
            )
            print(
                f"Ozon cookies loaded: {len(cookies)}"
            )
            print(
                "Ozon cookie names: "
                f"{extract_cookie_names(cookies)}"
            )

        except Exception:
            OZON_BROWSER_WORKER_READY.set(0)
            OZON_BROWSER_LIFECYCLE_TOTAL.labels(
                event="start_error",
            ).inc()
            self._cleanup()
            raise

        finally:
            OZON_BROWSER_START_DURATION_SECONDS.observe(
                time.monotonic() - started_at
            )

    @staticmethod
    def _route_handler(
        route: Route,
        request: Request,
    ) -> None:
        blocked_resource_types = {
            "image",
            "media",
            "font",
        }

        if request.resource_type in blocked_resource_types:
            route.abort()
            return

        route.continue_()

    def is_ready(self) -> bool:
        return bool(
            self.context is not None
            and self.browser is not None
            and self.browser.is_connected()
        )

    def new_page(self) -> Page:
        if not self.is_ready() or self.context is None:
            raise RuntimeError(
                "Browser context is not started"
            )

        OZON_BROWSER_PAGE_EVENTS_TOTAL.labels(
            event="create_attempt",
        ).inc()

        try:
            page = self.context.new_page()
            page.set_default_timeout(5_000)
            page.set_default_navigation_timeout(30_000)

            OZON_BROWSER_PAGES_ACTIVE.inc()
            OZON_BROWSER_PAGE_EVENTS_TOTAL.labels(
                event="created",
            ).inc()

            return page

        except Exception:
            OZON_BROWSER_PAGE_EVENTS_TOTAL.labels(
                event="create_error",
            ).inc()
            raise

    @staticmethod
    def close_page(page: Page) -> None:
        try:
            page.close()
            OZON_BROWSER_PAGE_EVENTS_TOTAL.labels(
                event="closed",
            ).inc()
        except Exception:
            OZON_BROWSER_PAGE_EVENTS_TOTAL.labels(
                event="close_error",
            ).inc()
        finally:
            OZON_BROWSER_PAGES_ACTIVE.dec()

    def stop(self) -> None:
        was_started = any(
            item is not None
            for item in (
                self.context,
                self.browser,
                self.playwright,
            )
        )

        OZON_BROWSER_WORKER_READY.set(0)
        self._cleanup()

        if was_started:
            OZON_BROWSER_LIFECYCLE_TOTAL.labels(
                event="stop",
            ).inc()

    def _cleanup(self) -> None:
        if self.context is not None:
            try:
                self.context.close()
            except Exception:
                pass
            finally:
                self.context = None

        if self.browser is not None:
            try:
                self.browser.close()
            except Exception:
                pass
            finally:
                self.browser = None

        if self.playwright is not None:
            try:
                self.playwright.stop()
            except Exception:
                pass
            finally:
                self.playwright = None
