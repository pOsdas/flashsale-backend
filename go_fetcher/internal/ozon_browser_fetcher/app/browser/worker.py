import os
import queue
import threading
import time
import traceback
from typing import Any, Dict, List, Optional
from datetime import datetime
from pathlib import Path

from ozon_browser_fetcher.app.browser.errors import OzonAntibotRejectedError
from ozon_browser_fetcher.app.browser.manager import BrowserManager
from ozon_browser_fetcher.app.metrics import (
    OZON_BROWSER_LAST_SUCCESS_TIMESTAMP_SECONDS,
    OZON_BROWSER_PRODUCTS_RETURNED_TOTAL,
    OZON_BROWSER_QUEUE_SIZE,
    OZON_BROWSER_TASK_EXECUTION_DURATION_SECONDS,
    OZON_BROWSER_TASK_EXECUTIONS_TOTAL,
    OZON_BROWSER_TASK_QUEUE_WAIT_SECONDS,
    OZON_BROWSER_TASK_REQUEST_DURATION_SECONDS,
    OZON_BROWSER_TASK_REQUESTS_TOTAL,
    OZON_BROWSER_TASKS_IN_PROGRESS,
    OZON_BROWSER_WORKER_READY,
    OZON_BROWSER_WORKER_RUNNING,
    classify_error,
    normalize_task_type,
    observe_worker_heartbeat,
)
from ozon_browser_fetcher.app.models.product import Product
from ozon_browser_fetcher.app.parsers.category_parser import (
    parse_category_from_page,
)
from ozon_browser_fetcher.app.parsers.search_parser import (
    parse_search_from_page,
)
from ozon_browser_fetcher.app.parsers.product_parser import (
    parse_product_from_page,
)


class BrowserWorker:
    def __init__(self, cookie_path: str) -> None:
        self.cookie_path = cookie_path
        self.manager = BrowserManager(cookie_path=cookie_path)

        self.task_queue: queue.Queue = queue.Queue()
        self.ready_event = threading.Event()
        self.stop_event = threading.Event()

        self.thread: Optional[threading.Thread] = None
        self.startup_error: Optional[str] = None

        self.diagnostics_dir = Path(
            os.getenv(
                "OZON_BROWSER_DIAGNOSTICS_DIR",
                "/tmp/ozon-parser-diagnostics",
            )
        )
        self.diagnostics_max_sets = self._env_int(
            "OZON_BROWSER_DIAGNOSTICS_MAX_SETS",
            10,
        )
        self.diagnostics_max_age_hours = self._env_int(
            "OZON_BROWSER_DIAGNOSTICS_MAX_AGE_HOURS",
            72,
        )
        self.diagnostics_max_total_mb = self._env_int(
            "OZON_BROWSER_DIAGNOSTICS_MAX_TOTAL_MB",
            100,
        )

    def start(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return

        self.stop_event.clear()
        self.ready_event.clear()
        self.startup_error = None

        self.thread = threading.Thread(
            target=self._run,
            name="ozon-browser-worker",
            daemon=True,
        )
        self.thread.start()

        is_ready = self.ready_event.wait(timeout=90)

        if not is_ready:
            raise RuntimeError(
                "Ozon browser worker did not start in time"
            )

        if self.startup_error is not None:
            raise RuntimeError(self.startup_error)

    def _run(self) -> None:
        OZON_BROWSER_WORKER_RUNNING.set(1)
        OZON_BROWSER_WORKER_READY.set(1)
        observe_worker_heartbeat()
        self.ready_event.set()

        try:
            while not self.stop_event.is_set():
                observe_worker_heartbeat()
                self._update_queue_size()

                try:
                    task = self.task_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                self._update_queue_size()
                self._execute_task(task)

        finally:
            OZON_BROWSER_WORKER_RUNNING.set(0)
            OZON_BROWSER_WORKER_READY.set(0)
            self.manager.stop()
            self._update_queue_size()

    def _execute_task(self, task: Dict[str, Any]) -> None:
        task_type = normalize_task_type(task.get("type"))
        started_at = time.monotonic()
        submitted_at = float(
            task.get("submitted_at_monotonic")
            or started_at
        )

        OZON_BROWSER_TASK_QUEUE_WAIT_SECONDS.labels(
            task_type=task_type,
        ).observe(
            max(0.0, started_at - submitted_at)
        )
        OZON_BROWSER_TASKS_IN_PROGRESS.labels(
            task_type=task_type,
        ).inc()

        try:
            self.manager.ensure_session(
                proxy_url=str(task.get("proxy_url") or ""),
                session_id=str(task.get("vpn_session_id") or ""),
                profile_id=str(task.get("browser_profile_id") or ""),
            )

            if task_type == "product":
                result = self._handle_product(task)
            elif task_type == "search":
                result = self._handle_search(task)
            elif task_type == "category":
                result = self._handle_category(task)
            else:
                raise RuntimeError(
                    f"Unknown task type: {task_type}"
                )

            products_count = self._count_products(
                task_type=task_type,
                result=result,
            )

            OZON_BROWSER_TASK_EXECUTIONS_TOTAL.labels(
                task_type=task_type,
                result="success",
                error_type="none",
            ).inc()
            OZON_BROWSER_PRODUCTS_RETURNED_TOTAL.labels(
                task_type=task_type,
            ).inc(products_count)
            OZON_BROWSER_LAST_SUCCESS_TIMESTAMP_SECONDS.labels(
                task_type=task_type,
            ).set(time.time())

            task["result_queue"].put(
                {
                    "ok": True,
                    "data": result,
                }
            )

        except OzonAntibotRejectedError as exc:
            OZON_BROWSER_TASK_EXECUTIONS_TOTAL.labels(
                task_type=task_type,
                result="error",
                error_type="marketplace_rejected",
            ).inc()

            task["result_queue"].put(
                {
                    "ok": False,
                    "status": "marketplace_rejected",
                    "error_type": "antibot",
                    "error": str(exc),
                    "trace": traceback.format_exc(),
                }
            )

        except Exception as exc:
            OZON_BROWSER_TASK_EXECUTIONS_TOTAL.labels(
                task_type=task_type,
                result="error",
                error_type=classify_error(exc),
            ).inc()

            task["result_queue"].put(
                {
                    "ok": False,
                    "status": "error",
                    "error_type": classify_error(exc),
                    "error": str(exc),
                    "trace": traceback.format_exc(),
                }
            )

        finally:
            OZON_BROWSER_TASK_EXECUTION_DURATION_SECONDS.labels(
                task_type=task_type,
            ).observe(
                time.monotonic() - started_at
            )
            OZON_BROWSER_TASKS_IN_PROGRESS.labels(
                task_type=task_type,
            ).dec()
            self.task_queue.task_done()
            self._update_queue_size()
            observe_worker_heartbeat()

    @staticmethod
    def _count_products(
        *,
        task_type: str,
        result: Any,
    ) -> int:
        if task_type == "product":
            return 1 if isinstance(result, dict) else 0

        if isinstance(result, list):
            return len(result)

        return 0

    def _handle_product(
        self,
        task: Dict[str, Any],
    ) -> Dict[str, Any]:
        url = str(task.get("url") or "").strip()

        if not url:
            raise RuntimeError("url is required")

        page = self.manager.new_page()

        try:
            product = parse_product_from_page(page, url)

            return product_to_dict(product)
        except Exception:
            self.diagnostics_dir.mkdir(
                parents=True,
                exist_ok=True,
            )
            self._cleanup_diagnostics()

            timestamp = datetime.now().strftime(
                "%Y%m%d-%H%M%S-%f"
            )
            html_path = (
                    self.diagnostics_dir
                    / f"{timestamp}.html"
            )
            screenshot_path = (
                    self.diagnostics_dir
                    / f"{timestamp}.png"
            )
            text_path = (
                    self.diagnostics_dir
                    / f"{timestamp}.txt"
            )

            try:
                html_path.write_text(
                    page.content(),
                    encoding="utf-8",
                )
            except Exception:
                pass

            try:
                page.screenshot(
                    path=str(screenshot_path),
                    full_page=True,
                )
            except Exception:
                pass

            try:
                body_text = page.locator("body").inner_text(timeout=3_000)
                text_path.write_text(
                    "\n".join(
                        [
                            f"requested_url={url}",
                            f"current_url={page.url}",
                            f"title={page.title()}",
                            "",
                            body_text,
                        ]
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass

            self._cleanup_diagnostics()

            print(
                "Ozon parser diagnostics saved: "
                f"prefix={self.diagnostics_dir / timestamp}",
                flush=True,
            )
            raise
        finally:
            self.manager.close_page(page)

    def _handle_search(
        self,
        task: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        query = str(task.get("query") or "").strip()
        limit = int(task.get("limit") or 10)

        if not query:
            raise RuntimeError("query is required")

        page = self.manager.new_page()

        try:
            products = parse_search_from_page(
                page=page,
                query=query,
                limit=limit,
            )

            return products_to_dicts(products)
        finally:
            self.manager.close_page(page)

    def _handle_category(
        self,
        task: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        url = str(task.get("url") or "").strip()
        limit = int(task.get("limit") or 10)

        if not url:
            raise RuntimeError("url is required")

        page = self.manager.new_page()

        try:
            products = parse_category_from_page(
                page=page,
                url=url,
                limit=limit,
            )

            return products_to_dicts(products)
        finally:
            self.manager.close_page(page)

    def submit_task(
        self,
        task: Dict[str, Any],
        timeout_seconds: int = 90,
    ) -> Dict[str, Any]:
        task_type = normalize_task_type(task.get("type"))
        started_at = time.monotonic()

        try:
            if self.startup_error is not None:
                OZON_BROWSER_TASK_REQUESTS_TOTAL.labels(
                    task_type=task_type,
                    result="startup_error",
                ).inc()
                raise RuntimeError(self.startup_error)

            worker_thread_alive = bool(
                self.thread is not None
                and self.thread.is_alive()
            )
            if not worker_thread_alive:
                OZON_BROWSER_TASK_REQUESTS_TOTAL.labels(
                    task_type=task_type,
                    result="worker_unavailable",
                ).inc()
                raise RuntimeError(
                    "Ozon browser worker thread is not running"
                )

            result_queue: queue.Queue = queue.Queue(maxsize=1)
            task["result_queue"] = result_queue
            task["submitted_at_monotonic"] = time.monotonic()

            self.task_queue.put(task)
            self._update_queue_size()

            try:
                result = result_queue.get(
                    timeout=timeout_seconds
                )
            except queue.Empty as exc:
                OZON_BROWSER_TASK_REQUESTS_TOTAL.labels(
                    task_type=task_type,
                    result="timeout",
                ).inc()
                raise TimeoutError(
                    "Ozon browser task timed out: "
                    f"type={task_type}, "
                    f"timeout={timeout_seconds}"
                ) from exc

            request_result = (
                "success"
                if result.get("ok")
                else "error"
            )
            OZON_BROWSER_TASK_REQUESTS_TOTAL.labels(
                task_type=task_type,
                result=request_result,
            ).inc()

            return result

        finally:
            OZON_BROWSER_TASK_REQUEST_DURATION_SECONDS.labels(
                task_type=task_type,
            ).observe(
                time.monotonic() - started_at
            )

    def parse_product(
        self,
        url: str,
        timeout_seconds: int = 90,
        proxy_url: str = "",
        vpn_session_id: str = "",
        browser_profile_id: str = "",
    ) -> Dict[str, Any]:
        return self.submit_task(
            task={
                "type": "product",
                "url": url,
                "proxy_url": proxy_url,
                "vpn_session_id": vpn_session_id,
                "browser_profile_id": browser_profile_id,
            },
            timeout_seconds=timeout_seconds,
        )

    def parse_search(
        self,
        query: str,
        limit: int = 10,
        timeout_seconds: int = 90,
        proxy_url: str = "",
        vpn_session_id: str = "",
        browser_profile_id: str = "",
    ) -> Dict[str, Any]:
        return self.submit_task(
            task={
                "type": "search",
                "query": query,
                "limit": limit,
                "proxy_url": proxy_url,
                "vpn_session_id": vpn_session_id,
                "browser_profile_id": browser_profile_id,
            },
            timeout_seconds=timeout_seconds,
        )

    def parse_category(
        self,
        url: str,
        limit: int = 10,
        timeout_seconds: int = 90,
        proxy_url: str = "",
        vpn_session_id: str = "",
        browser_profile_id: str = "",
    ) -> Dict[str, Any]:
        return self.submit_task(
            task={
                "type": "category",
                "url": url,
                "limit": limit,
                "proxy_url": proxy_url,
                "vpn_session_id": vpn_session_id,
                "browser_profile_id": browser_profile_id,
            },
            timeout_seconds=timeout_seconds,
        )

    def is_healthy(self) -> bool:
        return bool(
            self.startup_error is None
            and self.thread is not None
            and self.thread.is_alive()
            and self.manager.configuration_ready()
        )

    def get_health_snapshot(self) -> Dict[str, Any]:
        thread_alive = bool(
            self.thread is not None
            and self.thread.is_alive()
        )
        browser_ready = self.manager.is_ready()
        healthy = self.is_healthy()

        return {
            "status": "ok" if healthy else "error",
            "worker_thread_alive": thread_alive,
            "configuration_ready": self.manager.configuration_ready(),
            "browser_ready": browser_ready,
            "lazy_start": True,
            "browser_engine": "google-chrome-stable-cdp",
            "cdp_url": self.manager.runtime.config.cdp_url,
            "queue_size": self.task_queue.qsize(),
            "startup_error": self.startup_error is not None,
            "proxy_enabled": bool(self.manager.proxy_url),
            "vpn_session_id": self.manager.session_id,
            "browser_profile_id": self.manager.profile_id,
        }

    def stop(self) -> None:
        self.stop_event.set()

        if (
            self.thread is not None
            and self.thread.is_alive()
        ):
            self.thread.join(timeout=10)

    @staticmethod
    def _env_int(
            name: str,
            default: int,
    ) -> int:
        raw_value = os.getenv(name)

        if raw_value is None or not raw_value.strip():
            return default

        try:
            return max(1, int(raw_value))
        except ValueError:
            return default

    def _cleanup_diagnostics(self) -> None:
        if not self.diagnostics_dir.exists():
            return

        now = time.time()
        max_age_seconds = self.diagnostics_max_age_hours * 60 * 60

        files = [
            path
            for path in self.diagnostics_dir.iterdir()
            if path.is_file()
        ]

        # Удаляем файлы старше заданного срока.
        for path in files:
            try:
                if now - path.stat().st_mtime > max_age_seconds:
                    path.unlink()
            except OSError as exc:
                print(
                    "Ozon old diagnostic cleanup failed: "
                    f"path={path}, error={exc}",
                    flush=True,
                )

        files = [
            path
            for path in self.diagnostics_dir.iterdir()
            if path.is_file()
        ]

        # У каждого события одинаковое имя без расширения.
        diagnostic_sets: Dict[str, List[Path]] = {}

        for path in files:
            diagnostic_sets.setdefault(
                path.stem,
                [],
            ).append(path)

        ordered_sets = sorted(
            diagnostic_sets.values(),
            key=lambda paths: max(
                path.stat().st_mtime
                for path in paths
            ),
            reverse=True,
        )

        # Оставляем только последние N событий.
        for paths in ordered_sets[self.diagnostics_max_sets:]:
            for path in paths:
                try:
                    path.unlink()
                except OSError as exc:
                    print(
                        "Ozon excess diagnostic cleanup failed: "
                        f"path={path}, error={exc}",
                        flush=True,
                    )

        remaining_files = sorted(
            (
                path
                for path in self.diagnostics_dir.iterdir()
                if path.is_file()
            ),
            key=lambda path: path.stat().st_mtime,
        )

        max_total_bytes = (
                self.diagnostics_max_total_mb
                * 1024
                * 1024
        )

        total_bytes = sum(
            path.stat().st_size
            for path in remaining_files
        )

        # Жёсткое ограничение общего размера директории.
        while remaining_files and total_bytes > max_total_bytes:
            oldest_path = remaining_files.pop(0)

            try:
                file_size = oldest_path.stat().st_size
                oldest_path.unlink()
                total_bytes -= file_size
            except OSError as exc:
                print(
                    "Ozon diagnostic size cleanup failed: "
                    f"path={oldest_path}, error={exc}",
                    flush=True,
                )

    def _update_queue_size(self) -> None:
        OZON_BROWSER_QUEUE_SIZE.set(
            self.task_queue.qsize()
        )


def product_to_dict(product: Product) -> Dict[str, Any]:
    return {
        "external_id": product.sku,
        "sku": product.sku,
        "title": product.title,
        "seller_name": product.seller_name,
        "brand": product.brand,
        "price_cents": product.price_cents,
        "old_price_cents": product.old_price_cents,
        "currency": product.currency,
        "is_available": product.available > 0,
        "available": product.available,
        "is_active": product.is_active,
        "rating": product.rating,
        "reviews_count": product.reviews_count,
        "url": product.url,
        "product_path": product.product_path,
    }


def products_to_dicts(
    products: List[Product],
) -> List[Dict[str, Any]]:
    return [
        product_to_dict(product)
        for product in products
    ]


_browser_worker: Optional[BrowserWorker] = None


def init_browser_worker(
    cookie_path: str,
) -> BrowserWorker:
    global _browser_worker

    if _browser_worker is None:
        _browser_worker = BrowserWorker(
            cookie_path=cookie_path
        )
        _browser_worker.start()

    return _browser_worker


def get_browser_worker() -> BrowserWorker:
    if _browser_worker is None:
        raise RuntimeError(
            "Ozon browser worker is not initialized"
        )

    return _browser_worker


def shutdown_browser_worker() -> None:
    if _browser_worker is not None:
        _browser_worker.stop()
