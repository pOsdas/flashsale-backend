import os
import time
from typing import Optional

from playwright.sync_api import BrowserContext, Page, Request, Route

from browser_runtime.chrome_cdp import ChromeCDPConfiguration, ChromeCDPSession
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


_TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    return raw_value.strip().lower() in _TRUE_VALUES


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return max(minimum, value)


class BrowserManager:
    def __init__(self, cookie_path: str = "") -> None:
        self.runtime = ChromeCDPSession(
            ChromeCDPConfiguration.from_environment("ozon")
        )
        self.context: Optional[BrowserContext] = None
        self.cookie_path = cookie_path
        self.proxy_url = ""
        self.session_id = ""
        self.profile_id = ""
        self.action_timeout_ms = _env_int(
            "OZON_BROWSER_ACTION_TIMEOUT_MS",
            8_000,
        )
        self.navigation_timeout_ms = _env_int(
            "OZON_BROWSER_NAVIGATION_TIMEOUT_MS",
            45_000,
        )

    def start(
        self,
        cookie_path: str,
        proxy_url: str = "",
        session_id: str = "",
        profile_id: str = "",
    ) -> None:
        if (
            self.is_ready()
            and self.proxy_url == proxy_url.strip()
            and self.session_id == session_id.strip()
            and self.profile_id == profile_id.strip()
        ):
            return

        self.cookie_path = cookie_path
        self.proxy_url = proxy_url.strip()
        self.session_id = session_id.strip()
        self.profile_id = profile_id.strip()
        started_at = time.monotonic()

        try:
            self.context = self.runtime.start(
                proxy_url=self.proxy_url,
                session_id=self.session_id,
                profile_id=self.profile_id,
            )
            if _env_bool("OZON_BROWSER_BLOCK_HEAVY_RESOURCES", False):
                self.context.route("**/*", self._route_handler)

            cookies = self._load_cookies(cookie_path)
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
                "Ozon Google Chrome connected over CDP. "
                f"cdp_url={self.runtime.config.cdp_url}, "
                f"proxy_enabled={bool(self.proxy_url)}, "
                f"session={self.session_id or 'direct'}, "
                f"profile_id={self.profile_id or self.session_id or 'direct'}, "
                f"profile_dir={self.runtime.profile_dir}",
                flush=True,
            )
            print(
                f"Ozon cookies loaded: {len(cookies)}",
                flush=True,
            )
            print(
                "Ozon cookie names: "
                f"{extract_cookie_names(cookies)}",
                flush=True,
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

    def ensure_session(
        self,
        *,
        proxy_url: str = "",
        session_id: str = "",
        profile_id: str = "",
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

        cookie_path = self.cookie_path
        if not cookie_path:
            raise RuntimeError("Ozon cookie path is not configured")

        self.stop()
        self.start(
            cookie_path=cookie_path,
            proxy_url=normalized_proxy,
            session_id=normalized_session,
            profile_id=normalized_profile,
        )

    def is_ready(self) -> bool:
        return bool(self.context is not None and self.runtime.is_ready())

    def configuration_ready(self) -> bool:
        if not self.cookie_path:
            return False
        try:
            cookies = self._load_cookies(self.cookie_path)
        except (OSError, ValueError):
            return False
        return bool(cookies) or not _env_bool(
            "OZON_BROWSER_IMPORT_COOKIE_FILE",
            True,
        )

    def new_page(self) -> Page:
        if not self.is_ready() or self.context is None:
            raise RuntimeError("Browser context is not started")

        OZON_BROWSER_PAGE_EVENTS_TOTAL.labels(
            event="create_attempt",
        ).inc()

        try:
            page = self.context.new_page()
            page.set_default_timeout(self.action_timeout_ms)
            page.set_default_navigation_timeout(
                self.navigation_timeout_ms
            )

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
        was_started = self.context is not None or self.runtime.is_ready()
        OZON_BROWSER_WORKER_READY.set(0)
        self._cleanup()

        if was_started:
            OZON_BROWSER_LIFECYCLE_TOTAL.labels(
                event="stop",
            ).inc()

    def _cleanup(self) -> None:
        self.context = None
        self.runtime.stop()
        self.proxy_url = ""
        self.session_id = ""
        self.profile_id = ""

    @staticmethod
    def _route_handler(route: Route, request: Request) -> None:
        if request.resource_type in {"image", "media", "font"}:
            route.abort()
            return
        route.continue_()

    @staticmethod
    def _load_cookies(cookie_path: str) -> list[dict]:
        if not _env_bool("OZON_BROWSER_IMPORT_COOKIE_FILE", True):
            return []

        excluded_names = {
            name.strip()
            for name in os.getenv(
                "OZON_BROWSER_COOKIE_IMPORT_EXCLUDE_NAMES",
                "",
            ).split(",")
            if name.strip()
        }
        cookies = parse_cookie_header(load_cookie_header(cookie_path))
        if not excluded_names:
            return cookies
        return [
            cookie
            for cookie in cookies
            if cookie.get("name") not in excluded_names
        ]
