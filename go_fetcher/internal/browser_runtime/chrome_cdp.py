import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, TextIO

from playwright.sync_api import Browser, BrowserContext, Playwright, sync_playwright


_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default

    normalized = raw_value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False

    raise ValueError(
        f"Environment variable {name} must be a boolean, got {raw_value!r}"
    )


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(
            f"Environment variable {name} must be an integer, got {raw_value!r}"
        ) from exc

    if value < minimum:
        raise ValueError(
            f"Environment variable {name} must be >= {minimum}, got {value}"
        )
    return value


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default

    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(
            f"Environment variable {name} must be a number, got {raw_value!r}"
        ) from exc

    if value < minimum:
        raise ValueError(
            f"Environment variable {name} must be >= {minimum}, got {value}"
        )
    return value


def _safe_session_name(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip())
    normalized = normalized.strip("-._")
    return normalized[:120] or "default"


def _parse_window_size(value: str) -> tuple[int, int]:
    parts = [part.strip() for part in value.split(",", 1)]
    if len(parts) != 2:
        raise ValueError(
            "BROWSER_WINDOW_SIZE must have the format WIDTH,HEIGHT"
        )

    try:
        width = int(parts[0])
        height = int(parts[1])
    except ValueError as exc:
        raise ValueError(
            "BROWSER_WINDOW_SIZE must contain integer values"
        ) from exc

    if width < 800 or height < 600:
        raise ValueError(
            "BROWSER_WINDOW_SIZE must be at least 800,600"
        )
    return width, height


def _terminate_process_group(
    process: Optional[subprocess.Popen],
    timeout_seconds: float = 5.0,
) -> None:
    if process is None or process.poll() is not None:
        return

    try:
        process_group = os.getpgid(process.pid)
    except ProcessLookupError:
        return

    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        return

    try:
        process.wait(timeout=timeout_seconds)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        return

    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        pass


@dataclass(frozen=True, slots=True)
class ChromeCDPConfiguration:
    marketplace: str
    executable: str
    cdp_host: str
    cdp_port: int
    external_cdp_url: str
    connect_timeout_ms: int
    start_timeout_seconds: float
    retry_delay_seconds: float
    display: str
    window_width: int
    window_height: int
    language: str
    timezone: str
    runtime_dir: Path
    profile_root: Path
    profile_retention: int
    profile_cache_cleanup_interval_hours: int
    profile_max_total_mb: int
    runtime_log_retention: int
    disable_sandbox: bool
    disable_dev_shm_usage: bool
    headless: bool
    require_proxy: bool
    proxy_server_fallback: str
    proxy_bypass_list: str
    extra_args: tuple[str, ...]

    @classmethod
    def from_environment(cls, marketplace: str) -> "ChromeCDPConfiguration":
        normalized_marketplace = marketplace.strip().upper()
        if normalized_marketplace not in {"OZON", "WB"}:
            raise ValueError(
                f"Unsupported browser marketplace: {marketplace!r}"
            )

        width, height = _parse_window_size(
            os.getenv("BROWSER_WINDOW_SIZE", "1400,900")
        )
        prefix = f"{normalized_marketplace}_BROWSER_"

        return cls(
            marketplace=normalized_marketplace.lower(),
            executable=os.getenv(
                "BROWSER_EXECUTABLE",
                "/usr/bin/google-chrome-stable",
            ).strip(),
            cdp_host=os.getenv("BROWSER_CDP_HOST", "127.0.0.1").strip(),
            cdp_port=_env_int("BROWSER_CDP_PORT", 9222, minimum=1),
            external_cdp_url=os.getenv(
                f"{prefix}CDP_URL",
                "",
            ).strip(),
            connect_timeout_ms=_env_int(
                f"{prefix}CDP_CONNECT_TIMEOUT_MS",
                15_000,
                minimum=1,
            ),
            start_timeout_seconds=_env_float(
                f"{prefix}CDP_START_TIMEOUT_SECONDS",
                _env_float("BROWSER_START_TIMEOUT_SECONDS", 30.0),
                minimum=1.0,
            ),
            retry_delay_seconds=_env_float(
                f"{prefix}CDP_RETRY_DELAY_SECONDS",
                1.0,
                minimum=0.05,
            ),
            display=os.getenv("BROWSER_DISPLAY", ":99").strip(),
            window_width=width,
            window_height=height,
            language=os.getenv("BROWSER_LANGUAGE", "ru-RU").strip(),
            timezone=os.getenv("TZ", "Europe/Moscow").strip(),
            runtime_dir=Path(
                os.getenv("BROWSER_RUNTIME_DIR", "/tmp/browser-runtime")
            ),
            profile_root=Path(
                os.getenv("BROWSER_PROFILE_DIR", "/data/browser-profile")
            ),
            profile_retention=_env_int(
                "BROWSER_PROFILE_RETENTION",
                20,
                minimum=1,
            ),
            profile_cache_cleanup_interval_hours=_env_int(
                "BROWSER_PROFILE_CACHE_CLEANUP_INTERVAL_HOURS",
                24,
                minimum=1,
            ),
            profile_max_total_mb=_env_int(
                "BROWSER_PROFILE_MAX_TOTAL_MB",
                1024,
                minimum=64,
            ),
            runtime_log_retention=_env_int(
                "BROWSER_RUNTIME_LOG_RETENTION",
                20,
                minimum=4,
            ),
            disable_sandbox=_env_bool("BROWSER_DISABLE_SANDBOX", True),
            disable_dev_shm_usage=_env_bool(
                "BROWSER_DISABLE_DEV_SHM_USAGE",
                False,
            ),
            headless=_env_bool("BROWSER_HEADLESS", False),
            require_proxy=_env_bool("BROWSER_REQUIRE_PROXY", True),
            proxy_server_fallback=os.getenv(
                "BROWSER_PROXY_SERVER",
                "",
            ).strip(),
            proxy_bypass_list=os.getenv(
                "BROWSER_PROXY_BYPASS_LIST",
                "localhost;127.0.0.1",
            ).strip(),
            extra_args=tuple(
                shlex.split(os.getenv("BROWSER_EXTRA_ARGS", ""))
            ),
        )

    @property
    def cdp_url(self) -> str:
        if self.external_cdp_url:
            return self.external_cdp_url
        return f"http://{self.cdp_host}:{self.cdp_port}"


class ChromeCDPSession:
    def __init__(self, config: ChromeCDPConfiguration) -> None:
        self.config = config
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.chrome_process: Optional[subprocess.Popen] = None
        self.xvfb_process: Optional[subprocess.Popen] = None
        self.openbox_process: Optional[subprocess.Popen] = None
        self.chrome_stdout: Optional[TextIO] = None
        self.chrome_stderr: Optional[TextIO] = None
        self.xvfb_stdout: Optional[TextIO] = None
        self.xvfb_stderr: Optional[TextIO] = None
        self.openbox_stdout: Optional[TextIO] = None
        self.openbox_stderr: Optional[TextIO] = None
        self.proxy_url = ""
        self.session_id = ""
        self.profile_id = ""
        self.profile_dir: Optional[Path] = None

    def start(
        self,
        *,
        proxy_url: str,
        session_id: str,
        profile_id: str = "",
    ) -> BrowserContext:
        normalized_proxy = (
            proxy_url.strip() or self.config.proxy_server_fallback
        )
        normalized_session = session_id.strip()
        normalized_profile = (
            profile_id.strip() or normalized_session or "direct"
        )

        if self.config.require_proxy and not normalized_proxy:
            raise RuntimeError(
                "Browser proxy is required, but vpn_controller did not provide one"
            )
        if self.config.external_cdp_url and normalized_proxy:
            raise RuntimeError(
                "An external CDP endpoint cannot guarantee the requested dynamic proxy"
            )

        if (
            self.is_ready()
            and self.proxy_url == normalized_proxy
            and self.session_id == normalized_session
            and self.profile_id == normalized_profile
            and self.context is not None
        ):
            return self.context

        self.stop()
        self.proxy_url = normalized_proxy
        self.session_id = normalized_session
        self.profile_id = normalized_profile

        self.config.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.config.profile_root.mkdir(parents=True, exist_ok=True)
        self._cleanup_old_runtime_logs()
        self.profile_dir = (
            self.config.profile_root
            / _safe_session_name(normalized_profile)
        )
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._remove_stale_profile_locks(self.profile_dir)
        self._cleanup_old_profiles(exclude=self.profile_dir)
        self._cleanup_profile_caches()
        self._enforce_profile_size_limit(exclude=self.profile_dir)

        try:
            if not self.config.external_cdp_url:
                self._start_owned_browser()
                self._wait_for_cdp_endpoint()

            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.connect_over_cdp(
                self.config.cdp_url,
                timeout=self.config.connect_timeout_ms,
            )

            contexts = self.browser.contexts
            if not contexts:
                raise RuntimeError(
                    "Chrome CDP connection did not expose a default browser context"
                )
            self.context = contexts[0]
            return self.context
        except Exception:
            self.stop()
            raise

    def is_ready(self) -> bool:
        return bool(
            self.browser is not None
            and self.browser.is_connected()
            and self.context is not None
            and (
                self.config.external_cdp_url
                or (
                    self.chrome_process is not None
                    and self.chrome_process.poll() is None
                    and (
                        self.config.headless
                        or (
                            self.xvfb_process is not None
                            and self.xvfb_process.poll() is None
                            and (
                                self.openbox_process is None
                                or self.openbox_process.poll() is None
                            )
                        )
                    )
                )
            )
        )

    def stop(self) -> None:
        if self.browser is not None:
            try:
                if not self.config.external_cdp_url:
                    self.browser.close()
            except Exception:
                pass
            finally:
                self.browser = None
                self.context = None

        if self.playwright is not None:
            try:
                self.playwright.stop()
            except Exception:
                pass
            finally:
                self.playwright = None

        _terminate_process_group(self.chrome_process)
        self.chrome_process = None
        _terminate_process_group(self.openbox_process)
        self.openbox_process = None
        _terminate_process_group(self.xvfb_process)
        self.xvfb_process = None

        self._close_log_files()
        self.proxy_url = ""
        self.session_id = ""
        self.profile_id = ""
        self.profile_dir = None

    def _start_owned_browser(self) -> None:
        executable = self._resolve_executable()
        run_name = _safe_session_name(
            f"{self.config.marketplace}-{self.session_id or 'direct'}"
        )

        if not self.config.headless:
            self._start_xvfb(run_name)
            self._start_openbox(run_name)

        chrome_stdout_path = self.config.runtime_dir / f"{run_name}.chrome.out.log"
        chrome_stderr_path = self.config.runtime_dir / f"{run_name}.chrome.err.log"
        self.chrome_stdout = chrome_stdout_path.open("a", encoding="utf-8")
        self.chrome_stderr = chrome_stderr_path.open("a", encoding="utf-8")

        environment = os.environ.copy()
        environment["TZ"] = self.config.timezone
        if not self.config.headless:
            environment["DISPLAY"] = self.config.display

        command = self.build_chrome_command(executable=executable)
        self.chrome_process = subprocess.Popen(
            command,
            stdout=self.chrome_stdout,
            stderr=self.chrome_stderr,
            env=environment,
            start_new_session=True,
        )

    def build_chrome_command(self, *, executable: str) -> list[str]:
        if self.profile_dir is None:
            raise RuntimeError("Chrome profile directory is not initialized")

        command = [
            executable,
            f"--remote-debugging-address={self.config.cdp_host}",
            f"--remote-debugging-port={self.config.cdp_port}",
            f"--user-data-dir={self.profile_dir}",
            f"--window-size={self.config.window_width},{self.config.window_height}",
            f"--lang={self.config.language}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-search-engine-choice-screen",
            "--password-store=basic",
            "--disable-session-crashed-bubble",
        ]

        if self.config.headless:
            command.append("--headless=new")
        if self.config.disable_sandbox:
            command.extend(["--no-sandbox", "--disable-setuid-sandbox"])
        if self.config.disable_dev_shm_usage:
            command.append("--disable-dev-shm-usage")
        if self.proxy_url:
            command.append(f"--proxy-server={self.proxy_url}")
        if self.config.proxy_bypass_list:
            command.append(
                f"--proxy-bypass-list={self.config.proxy_bypass_list}"
            )

        command.extend(self.config.extra_args)
        command.append("about:blank")
        return command

    def _start_xvfb(self, run_name: str) -> None:
        display_number = self.config.display.lstrip(":").split(".", 1)[0]
        if not display_number.isdigit():
            raise ValueError(
                f"BROWSER_DISPLAY must look like :99, got {self.config.display!r}"
            )

        xvfb_stdout_path = self.config.runtime_dir / f"{run_name}.xvfb.out.log"
        xvfb_stderr_path = self.config.runtime_dir / f"{run_name}.xvfb.err.log"
        self.xvfb_stdout = xvfb_stdout_path.open("a", encoding="utf-8")
        self.xvfb_stderr = xvfb_stderr_path.open("a", encoding="utf-8")

        command = [
            "Xvfb",
            self.config.display,
            "-screen",
            "0",
            f"{self.config.window_width}x{self.config.window_height}x24",
            "-nolisten",
            "tcp",
            "-ac",
        ]
        self.xvfb_process = subprocess.Popen(
            command,
            stdout=self.xvfb_stdout,
            stderr=self.xvfb_stderr,
            start_new_session=True,
        )

        socket_path = Path(f"/tmp/.X11-unix/X{display_number}")
        deadline = time.monotonic() + min(
            10.0,
            self.config.start_timeout_seconds,
        )
        while time.monotonic() < deadline:
            if self.xvfb_process.poll() is not None:
                raise RuntimeError(
                    "Xvfb stopped before its display became ready"
                )
            if socket_path.exists():
                return
            time.sleep(0.1)

        raise TimeoutError(
            f"Xvfb display {self.config.display} did not become ready"
        )

    def _start_openbox(self, run_name: str) -> None:
        executable = shutil.which("openbox")
        if not executable:
            return

        openbox_stdout_path = (
            self.config.runtime_dir / f"{run_name}.openbox.out.log"
        )
        openbox_stderr_path = (
            self.config.runtime_dir / f"{run_name}.openbox.err.log"
        )
        self.openbox_stdout = openbox_stdout_path.open("a", encoding="utf-8")
        self.openbox_stderr = openbox_stderr_path.open("a", encoding="utf-8")

        environment = os.environ.copy()
        environment["DISPLAY"] = self.config.display
        self.openbox_process = subprocess.Popen(
            [executable, "--sm-disable"],
            stdout=self.openbox_stdout,
            stderr=self.openbox_stderr,
            env=environment,
            start_new_session=True,
        )
        time.sleep(0.5)
        if self.openbox_process.poll() is not None:
            raise RuntimeError("Openbox stopped immediately after startup")

    def _wait_for_cdp_endpoint(self) -> None:
        deadline = time.monotonic() + self.config.start_timeout_seconds
        version_url = f"{self.config.cdp_url.rstrip('/')}/json/version"
        last_error = ""

        while time.monotonic() < deadline:
            if self.chrome_process is not None:
                return_code = self.chrome_process.poll()
                if return_code is not None:
                    raise RuntimeError(
                        "Google Chrome stopped before CDP became ready: "
                        f"exit_code={return_code}"
                    )

            try:
                with urllib.request.urlopen(
                    version_url,
                    timeout=min(3.0, self.config.retry_delay_seconds + 1.0),
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if payload.get("webSocketDebuggerUrl"):
                    return
                last_error = "CDP response has no webSocketDebuggerUrl"
            except (
                urllib.error.URLError,
                TimeoutError,
                json.JSONDecodeError,
                UnicodeDecodeError,
            ) as exc:
                last_error = f"{type(exc).__name__}: {exc}"

            time.sleep(self.config.retry_delay_seconds)

        raise TimeoutError(
            "Google Chrome CDP endpoint did not become ready: "
            f"url={version_url}, last_error={last_error}"
        )

    def _resolve_executable(self) -> str:
        configured = self.config.executable
        configured_path = Path(configured)
        if configured_path.is_absolute() and configured_path.is_file():
            return str(configured_path)

        resolved = shutil.which(configured)
        if resolved:
            return resolved

        for candidate in (
            "google-chrome-stable",
            "google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/google-chrome",
        ):
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
            candidate_path = Path(candidate)
            if candidate_path.is_absolute() and candidate_path.is_file():
                return str(candidate_path)

        raise FileNotFoundError(
            "Google Chrome Stable executable was not found; "
            f"configured path={configured!r}"
        )

    @staticmethod
    def _remove_stale_profile_locks(profile_dir: Path) -> None:
        for name in (
            "SingletonCookie",
            "SingletonLock",
            "SingletonSocket",
            "DevToolsActivePort",
        ):
            path = profile_dir / name
            try:
                if path.is_symlink() or path.is_file():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
            except FileNotFoundError:
                pass

    def _cleanup_old_profiles(self, *, exclude: Path) -> None:
        directories = [
            path
            for path in self.config.profile_root.iterdir()
            if path.is_dir() and path != exclude
        ]
        directories.sort(
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )

        keep_count = max(0, self.config.profile_retention - 1)
        for directory in directories[keep_count:]:
            try:
                shutil.rmtree(directory)
            except OSError:
                pass

    _CACHE_DIRECTORY_NAMES = {
        "Cache",
        "Code Cache",
        "GPUCache",
        "GrShaderCache",
        "ShaderCache",
        "DawnCache",
        "GraphiteDawnCache",
        "BrowserMetrics",
        "Crashpad",
        "component_crx_cache",
        "optimization_guide_model_store",
    }
    _CACHE_CLEANUP_MARKER = ".flashsale-cache-cleanup"

    @staticmethod
    def _directory_size_bytes(directory: Path) -> int:
        total = 0

        try:
            for path in directory.rglob("*"):
                try:
                    if path.is_file() and not path.is_symlink():
                        total += path.stat().st_size
                except OSError:
                    continue
        except OSError:
            return total

        return total

    @staticmethod
    def _remove_path(path: Path) -> int:
        try:
            if not path.exists() and not path.is_symlink():
                return 0

            size_before = 0
            if path.is_dir() and not path.is_symlink():
                size_before = ChromeCDPSession._directory_size_bytes(path)
                shutil.rmtree(path)
            else:
                try:
                    size_before = path.stat().st_size
                except OSError:
                    size_before = 0
                path.unlink()

            return size_before
        except OSError:
            return 0

    def _profile_cache_paths(self, profile_dir: Path) -> list[Path]:
        candidates: list[Path] = []

        for name in self._CACHE_DIRECTORY_NAMES:
            candidates.append(profile_dir / name)

        try:
            children = list(profile_dir.iterdir())
        except OSError:
            return candidates

        for child in children:
            if not child.is_dir():
                continue

            if child.name == "Default" or child.name.startswith("Profile "):
                for name in self._CACHE_DIRECTORY_NAMES:
                    candidates.append(child / name)

        unique_paths: list[Path] = []
        seen: set[str] = set()

        for path in candidates:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            unique_paths.append(path)

        return unique_paths

    def _cleanup_profile_caches(self) -> None:
        max_total_bytes = self.config.profile_max_total_mb * 1024 * 1024
        current_total = self._directory_size_bytes(self.config.profile_root)
        force_cleanup = current_total > max_total_bytes

        try:
            profile_directories = [
                path
                for path in self.config.profile_root.iterdir()
                if path.is_dir()
            ]
        except OSError:
            return

        now = time.time()
        interval_seconds = (
            self.config.profile_cache_cleanup_interval_hours * 60 * 60
        )

        for profile_dir in profile_directories:
            marker_path = profile_dir / self._CACHE_CLEANUP_MARKER
            due = force_cleanup

            if not due:
                try:
                    due = (
                        not marker_path.exists()
                        or now - marker_path.stat().st_mtime >= interval_seconds
                    )
                except OSError:
                    due = True

            if not due:
                continue

            size_before = self._directory_size_bytes(profile_dir)
            removed_bytes = 0

            for cache_path in self._profile_cache_paths(profile_dir):
                removed_bytes += self._remove_path(cache_path)

            try:
                marker_path.touch(exist_ok=True)
            except OSError:
                pass

            size_after = self._directory_size_bytes(profile_dir)
            actual_removed = max(0, size_before - size_after)

            if actual_removed > 0 or removed_bytes > 0:
                print(
                    "Chrome profile cache cleaned: "
                    f"marketplace={self.config.marketplace}, "
                    f"profile={profile_dir.name}, "
                    f"removed_mb={actual_removed / 1024 / 1024:.2f}, "
                    f"size_after_mb={size_after / 1024 / 1024:.2f}",
                    flush=True,
                )

    def _enforce_profile_size_limit(self, *, exclude: Path) -> None:
        max_total_bytes = self.config.profile_max_total_mb * 1024 * 1024
        total_bytes = self._directory_size_bytes(self.config.profile_root)

        if total_bytes <= max_total_bytes:
            return

        try:
            directories = [
                path
                for path in self.config.profile_root.iterdir()
                if path.is_dir() and path != exclude
            ]
        except OSError:
            return

        directories.sort(
            key=lambda path: path.stat().st_mtime_ns,
        )

        for directory in directories:
            if total_bytes <= max_total_bytes:
                break

            directory_size = self._directory_size_bytes(directory)

            try:
                shutil.rmtree(directory)
            except OSError:
                continue

            total_bytes = max(0, total_bytes - directory_size)
            print(
                "Old Chrome profile removed to enforce size limit: "
                f"marketplace={self.config.marketplace}, "
                f"profile={directory.name}, "
                f"removed_mb={directory_size / 1024 / 1024:.2f}, "
                f"remaining_mb={total_bytes / 1024 / 1024:.2f}, "
                f"limit_mb={self.config.profile_max_total_mb}",
                flush=True,
            )

        if total_bytes > max_total_bytes:
            print(
                "Chrome profile size limit could not be fully enforced: "
                f"marketplace={self.config.marketplace}, "
                f"active_profile={exclude.name}, "
                f"current_mb={total_bytes / 1024 / 1024:.2f}, "
                f"limit_mb={self.config.profile_max_total_mb}",
                flush=True,
            )

    def _cleanup_old_runtime_logs(self) -> None:
        log_files = [
            path
            for path in self.config.runtime_dir.iterdir()
            if path.is_file() and path.name.endswith(".log")
        ]
        log_files.sort(
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for log_path in log_files[self.config.runtime_log_retention:]:
            try:
                log_path.unlink()
            except OSError:
                pass

    def _close_log_files(self) -> None:
        for attribute_name in (
            "chrome_stdout",
            "chrome_stderr",
            "xvfb_stdout",
            "xvfb_stderr",
            "openbox_stdout",
            "openbox_stderr",
        ):
            handle = getattr(self, attribute_name)
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
                setattr(self, attribute_name, None)
