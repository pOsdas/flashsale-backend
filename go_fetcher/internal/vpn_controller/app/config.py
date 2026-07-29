import os
from dataclasses import dataclass
from pathlib import Path


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _get_float(name: str, default: float, minimum: float = 0.1) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _get_url(name: str, default: str) -> str:
    return os.getenv(name, default).strip().rstrip("/")


@dataclass(frozen=True, slots=True)
class Settings:
    subscriptions_path: Path
    profiles_path: Path
    state_path: Path
    runs_dir: Path
    xray_binary: Path
    preflight_interval_seconds: int
    parse_delay_seconds: int
    preflight_attempts: int
    preflight_min_successes: int
    preflight_parallelism: int
    xray_start_timeout_seconds: float
    probe_timeout_seconds: float
    probe_urls: tuple[str, ...]
    max_profiles_per_group: int
    retained_runs: int
    run_preflight_on_start: bool
    gateway_enabled: bool = True
    require_parse_ready: bool = True
    parse_proxy_port: int = 10808
    parse_proxy_public_host: str = "vpn_controller"
    parse_worker_timeout_seconds: float = 90.0
    parse_attempt_timeout_seconds: int = 75
    active_session_idle_seconds: int = 1200
    marketplace_rejection_confirmations: int = 2
    ozon_browser_url: str = "http://ozon_browser_fetcher:8095"
    wb_browser_url: str = "http://wb_browser_fetcher:8096"

    @classmethod
    def from_env(cls) -> "Settings":
        probe_urls = tuple(
            item.strip()
            for item in os.getenv(
                "VPN_PROBE_URLS",
                "https://api.ipify.org,https://ifconfig.me/ip",
            ).split(",")
            if item.strip()
        )
        if not probe_urls:
            raise ValueError("VPN_PROBE_URLS must contain at least one URL")

        attempts = _get_int("VPN_PREFLIGHT_ATTEMPTS", 3)
        min_successes = _get_int("VPN_PREFLIGHT_MIN_SUCCESSES", 2)
        if min_successes > attempts:
            raise ValueError(
                "VPN_PREFLIGHT_MIN_SUCCESSES cannot exceed "
                "VPN_PREFLIGHT_ATTEMPTS"
            )

        return cls(
            subscriptions_path=Path(
                os.getenv(
                    "VPN_SUBSCRIPTIONS_PATH",
                    "/app/secrets/subscriptions.json",
                )
            ),
            profiles_path=Path(
                os.getenv(
                    "VPN_PROFILES_PATH",
                    "/app/vpn_controller/vpn_profiles.json",
                )
            ),
            state_path=Path(
                os.getenv(
                    "VPN_STATE_PATH",
                    "/app/state/latest-preflight.json",
                )
            ),
            runs_dir=Path(os.getenv("VPN_RUNS_DIR", "/app/state/runs")),
            xray_binary=Path(
                os.getenv("VPN_XRAY_BINARY", "/usr/local/bin/xray")
            ),
            preflight_interval_seconds=_get_int(
                "VPN_PREFLIGHT_INTERVAL_SECONDS", 3600
            ),
            parse_delay_seconds=_get_int(
                "VPN_PARSE_DELAY_SECONDS", 600, minimum=0
            ),
            preflight_attempts=attempts,
            preflight_min_successes=min_successes,
            preflight_parallelism=_get_int(
                "VPN_PREFLIGHT_PARALLELISM", 2
            ),
            xray_start_timeout_seconds=_get_float(
                "VPN_XRAY_START_TIMEOUT_SECONDS", 20.0
            ),
            probe_timeout_seconds=_get_float(
                "VPN_PROBE_TIMEOUT_SECONDS", 10.0
            ),
            probe_urls=probe_urls,
            max_profiles_per_group=_get_int(
                "VPN_MAX_PROFILES_PER_GROUP", 3
            ),
            retained_runs=_get_int("VPN_RETAINED_RUNS", 5),
            run_preflight_on_start=_get_bool(
                "VPN_RUN_PREFLIGHT_ON_START", True
            ),
            gateway_enabled=_get_bool("VPN_GATEWAY_ENABLED", True),
            require_parse_ready=_get_bool(
                "VPN_REQUIRE_PARSE_READY", True
            ),
            parse_proxy_port=_get_int("VPN_PARSE_PROXY_PORT", 10808),
            parse_proxy_public_host=os.getenv(
                "VPN_PARSE_PROXY_PUBLIC_HOST", "vpn_controller"
            ).strip(),
            parse_worker_timeout_seconds=_get_float(
                "VPN_PARSE_WORKER_TIMEOUT_SECONDS", 90.0
            ),
            parse_attempt_timeout_seconds=_get_int(
                "VPN_PARSE_ATTEMPT_TIMEOUT_SECONDS", 75, minimum=5
            ),
            active_session_idle_seconds=_get_int(
                "VPN_ACTIVE_SESSION_IDLE_SECONDS", 1200
            ),
            marketplace_rejection_confirmations=_get_int(
                "VPN_MARKETPLACE_REJECTION_CONFIRMATIONS", 2
            ),
            ozon_browser_url=_get_url(
                "VPN_OZON_BROWSER_URL",
                "http://ozon_browser_fetcher:8095",
            ),
            wb_browser_url=_get_url(
                "VPN_WB_BROWSER_URL",
                "http://wb_browser_fetcher:8096",
            ),
        )
