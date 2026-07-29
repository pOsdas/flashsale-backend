
import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from vpn_controller.app.config import Settings
from vpn_controller.app.metrics import (
    VPN_ACTIVE_SESSION,
    VPN_ACTIVE_SESSION_LAST_USED_TIMESTAMP_SECONDS,
)
from vpn_controller.app.models import VPNProfile
from vpn_controller.app.probe import ProxyProbe
from vpn_controller.app.service import slugify
from vpn_controller.app.xray_config import build_runtime_config
from vpn_controller.app.xray_process import XrayProcess


logger = logging.getLogger(__name__)


class ActiveSessionExitIPMismatchError(RuntimeError):
    pass


def build_browser_profile_id(profile: VPNProfile) -> str:
    canonical = json.dumps(
        {
            "name": profile.name,
            "occurrence": profile.occurrence,
            "outbound_tag": profile.outbound_tag,
            "config": profile.config,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()[:16]
    return f"{slugify(profile.name)[:48]}-{digest}"


@dataclass(frozen=True, slots=True)
class ActiveSessionSnapshot:
    cycle_id: str = ""
    profile_name: str = ""
    expected_exit_ip: str = ""
    actual_exit_ip: str = ""
    proxy_url: str = ""
    browser_session_id: str = ""
    browser_profile_id: str = ""
    running: bool = False
    last_used_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "cycle_id": self.cycle_id,
            "profile_name": self.profile_name,
            "expected_exit_ip": self.expected_exit_ip,
            "actual_exit_ip": self.actual_exit_ip,
            "proxy_url": self.proxy_url,
            "browser_session_id": self.browser_session_id,
            "browser_profile_id": self.browser_profile_id,
            "running": self.running,
            "last_used_at": self.last_used_at,
        }


class ActiveVPNParseSession:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._xray: XrayProcess | None = None
        self._profile: VPNProfile | None = None
        self._cycle_id = ""
        self._actual_exit_ip = ""
        self._browser_session_id = ""
        self._browser_profile_id = ""
        self._last_used_at = 0.0

    def activate(
        self,
        *,
        profile: VPNProfile,
        cycle_id: str,
    ) -> ActiveSessionSnapshot:
        with self._lock:
            if self._can_reuse(profile=profile, cycle_id=cycle_id):
                self.touch()
                return self.snapshot()

            self._stop_locked(reason="profile_switch")
            run_dir = (
                self.settings.runs_dir
                / cycle_id
                / "active-parse"
                / slugify(profile.name)
            )
            run_dir.mkdir(parents=True, exist_ok=True)

            config_path = run_dir / "runtime-config.json"
            access_log_path = run_dir / "xray-access.log"
            error_log_path = run_dir / "xray-error.log"
            stdout_path = run_dir / "xray.stdout.log"
            stderr_path = run_dir / "xray.stderr.log"

            runtime_config, _ = build_runtime_config(
                profile=profile,
                socks_port=self.settings.parse_proxy_port,
                access_log_path=str(access_log_path),
                error_log_path=str(error_log_path),
                listen_host="0.0.0.0",
            )
            config_path.write_text(
                json.dumps(runtime_config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            xray = XrayProcess(
                binary=self.settings.xray_binary,
                config_path=config_path,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )

            try:
                xray.validate()
                xray.start()
                xray.wait_for_port(
                    port=self.settings.parse_proxy_port,
                    timeout_seconds=self.settings.xray_start_timeout_seconds,
                )
                probe = ProxyProbe(
                    urls=self.settings.probe_urls,
                    timeout_seconds=self.settings.probe_timeout_seconds,
                )
                sample = probe.run(
                    socks_port=self.settings.parse_proxy_port
                )
                if sample.exit_ip != profile.expected_exit_ip:
                    raise ActiveSessionExitIPMismatchError(
                        "Active VPN exit IP mismatch: "
                        f"expected={profile.expected_exit_ip}, "
                        f"actual={sample.exit_ip}"
                    )
            except Exception:
                xray.stop()
                raise
            finally:
                try:
                    config_path.unlink(missing_ok=True)
                except OSError:
                    pass

            session_digest = hashlib.sha256(
                f"{cycle_id}:{profile.name}:{time.time_ns()}".encode("utf-8")
            ).hexdigest()[:16]

            self._xray = xray
            self._profile = profile
            self._cycle_id = cycle_id
            self._actual_exit_ip = sample.exit_ip
            self._browser_session_id = f"{cycle_id}-{session_digest}"
            self._browser_profile_id = build_browser_profile_id(profile)
            self.touch()

            VPN_ACTIVE_SESSION.labels(
                exit_ip=profile.expected_exit_ip,
                profile=profile.name,
            ).set(1)

            logger.info(
                "Active VPN parsing session started cycle_id=%s profile=%r "
                "exit_ip=%s proxy_port=%s",
                cycle_id,
                profile.name,
                sample.exit_ip,
                self.settings.parse_proxy_port,
            )
            return self.snapshot()

    def touch(self) -> None:
        self._last_used_at = time.time()
        VPN_ACTIVE_SESSION_LAST_USED_TIMESTAMP_SECONDS.set(
            self._last_used_at
        )

    def stop(self, reason: str = "manual") -> None:
        with self._lock:
            self._stop_locked(reason=reason)

    def stop_if_idle(self) -> bool:
        with self._lock:
            if self._xray is None or not self._last_used_at:
                return False
            idle_seconds = time.time() - self._last_used_at
            if idle_seconds < self.settings.active_session_idle_seconds:
                return False
            self._stop_locked(reason="idle_timeout")
            return True

    def snapshot(self) -> ActiveSessionSnapshot:
        with self._lock:
            profile = self._profile
            running = bool(self._xray and self._xray.is_running())
            proxy_url = ""
            if running:
                proxy_url = (
                    "socks5://"
                    f"{self.settings.parse_proxy_public_host}:"
                    f"{self.settings.parse_proxy_port}"
                )
            return ActiveSessionSnapshot(
                cycle_id=self._cycle_id,
                profile_name=profile.name if profile else "",
                expected_exit_ip=(
                    profile.expected_exit_ip if profile else ""
                ),
                actual_exit_ip=self._actual_exit_ip,
                proxy_url=proxy_url,
                browser_session_id=self._browser_session_id,
                browser_profile_id=self._browser_profile_id,
                running=running,
                last_used_at=self._last_used_at,
            )

    def _can_reuse(
        self,
        *,
        profile: VPNProfile,
        cycle_id: str,
    ) -> bool:
        return bool(
            self._xray is not None
            and self._xray.is_running()
            and self._profile is not None
            and self._profile.name == profile.name
            and self._profile.occurrence == profile.occurrence
            and self._cycle_id == cycle_id
        )

    def _stop_locked(self, reason: str) -> None:
        profile = self._profile
        if self._xray is not None:
            self._xray.stop()

        if profile is not None:
            VPN_ACTIVE_SESSION.labels(
                exit_ip=profile.expected_exit_ip,
                profile=profile.name,
            ).set(0)
            logger.info(
                "Active VPN parsing session stopped cycle_id=%s "
                "profile=%r reason=%s",
                self._cycle_id,
                profile.name,
                reason,
            )

        self._xray = None
        self._profile = None
        self._cycle_id = ""
        self._actual_exit_ip = ""
        self._browser_session_id = ""
        self._browser_profile_id = ""
        self._last_used_at = 0.0
