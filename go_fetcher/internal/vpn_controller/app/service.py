
import json
import logging
import re
import shutil
import statistics
import threading
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vpn_controller.app.config import Settings
from vpn_controller.app.metrics import (
    VPN_GROUP_AVAILABLE_PROFILES,
    VPN_GROUP_SELECTED_PROFILES,
    VPN_PARSE_READY_TIMESTAMP_SECONDS,
    VPN_PLAN_READY,
    VPN_PREFLIGHT_DURATION_SECONDS,
    VPN_PREFLIGHT_LAST_SUCCESS_TIMESTAMP_SECONDS,
    VPN_PREFLIGHT_RUNNING,
    VPN_PREFLIGHT_RUNS_TOTAL,
    VPN_PROFILE_AVAILABLE,
    VPN_PROFILE_CHECKS_TOTAL,
    VPN_PROFILE_MEDIAN_LATENCY_MILLISECONDS,
)
from vpn_controller.app.models import (
    PreflightPlan,
    PreflightResult,
    PreflightStatus,
    VPNProfile,
)
from vpn_controller.app.probe import ProxyProbe, ProxyProbeError
from vpn_controller.app.profile_loader import load_selected_profiles
from vpn_controller.app.ranking import build_group_plan
from vpn_controller.app.state import ControllerState
from vpn_controller.app.xray_config import (
    XrayConfigError,
    build_runtime_config,
)
from vpn_controller.app.xray_process import (
    XrayConfigRejectedError,
    XrayProcess,
    XrayStartError,
    get_free_port,
)


logger = logging.getLogger(__name__)


class PreflightAlreadyRunningError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def slugify(value: str) -> str:
    normalized = re.sub(
        r"[^a-zA-Z0-9а-яА-ЯёЁ._-]+", "-", value
    ).strip("-._")
    return normalized[:70] or "profile"


class VPNPreflightService:
    def __init__(
        self,
        settings: Settings,
        state: ControllerState,
    ) -> None:
        self.settings = settings
        self.state = state
        self._run_lock = threading.Lock()

    def run(self, cycle_started_at: datetime | None = None) -> PreflightPlan:
        if not self._run_lock.acquire(blocking=False):
            raise PreflightAlreadyRunningError(
                "VPN preflight is already running"
            )

        started_monotonic = time.monotonic()
        cycle_started_at = cycle_started_at or utc_now()
        cycle_id = cycle_started_at.strftime("%Y%m%dT%H%M%SZ")
        run_dir = self.settings.runs_dir / cycle_id
        run_dir.mkdir(parents=True, exist_ok=True)

        self.state.set_running(True)
        VPN_PREFLIGHT_RUNNING.set(1)

        try:
            profiles = load_selected_profiles(
                subscriptions_path=self.settings.subscriptions_path,
                profiles_path=self.settings.profiles_path,
            )
            logger.info(
                "Loaded VPN profiles cycle_id=%s profiles=%s "
                "parallelism=%s attempts=%s",
                cycle_id,
                len(profiles),
                self.settings.preflight_parallelism,
                self.settings.preflight_attempts,
            )
            self._reset_latest_metrics()

            results = self._run_profiles(
                profiles=profiles,
                run_dir=run_dir,
            )
            groups, unavailable = build_group_plan(
                results=results,
                max_profiles_per_group=(
                    self.settings.max_profiles_per_group
                ),
            )

            completed_at = utc_now()
            plan = PreflightPlan(
                cycle_id=cycle_id,
                cycle_started_at=isoformat(cycle_started_at),
                completed_at=isoformat(completed_at),
                parse_ready_at=isoformat(
                    cycle_started_at
                    + timedelta(
                        seconds=self.settings.parse_delay_seconds
                    )
                ),
                next_preflight_at=isoformat(
                    cycle_started_at
                    + timedelta(
                        seconds=(
                            self.settings.preflight_interval_seconds
                        )
                    )
                ),
                groups=groups,
                unavailable_profiles=unavailable,
            )

            self.state.set_plan(plan)
            self._write_run_result(run_dir=run_dir, plan=plan)
            self._update_plan_metrics(plan)
            self._cleanup_old_runs()
            logger.info(
                "VPN preflight plan ready cycle_id=%s group_order=%s",
                cycle_id,
                [
                    {
                        "exit_ip": group.exit_ip,
                        "selected_profiles": [
                            item.profile_name
                            for item in group.selected_profiles
                        ],
                    }
                    for group in groups
                ],
            )

            result_label = "success" if groups else "no_profiles"
            VPN_PREFLIGHT_RUNS_TOTAL.labels(result=result_label).inc()
            if groups:
                VPN_PREFLIGHT_LAST_SUCCESS_TIMESTAMP_SECONDS.set(
                    completed_at.timestamp()
                )

            return plan

        except Exception as exc:
            self.state.set_error(f"{type(exc).__name__}: {exc}")
            logger.exception(
                "VPN preflight run failed cycle_id=%s", cycle_id
            )
            VPN_PLAN_READY.set(0)
            VPN_PREFLIGHT_RUNS_TOTAL.labels(result="error").inc()
            (run_dir / "preflight-error.log").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
            raise

        finally:
            VPN_PREFLIGHT_RUNNING.set(0)
            self.state.set_running(False)
            VPN_PREFLIGHT_DURATION_SECONDS.observe(
                time.monotonic() - started_monotonic
            )
            self._run_lock.release()

    def _run_profiles(
        self,
        profiles: list[VPNProfile],
        run_dir: Path,
    ) -> list[PreflightResult]:
        results: list[PreflightResult] = []
        workers = min(
            self.settings.preflight_parallelism,
            len(profiles),
        )
        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="vpn-preflight",
        ) as executor:
            future_to_profile = {
                executor.submit(
                    self._check_profile,
                    profile,
                    run_dir / f"{index:02d}-{slugify(profile.name)}",
                ): profile
                for index, profile in enumerate(profiles, start=1)
            }
            for future in as_completed(future_to_profile):
                profile = future_to_profile[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = PreflightResult(
                        profile_name=profile.name,
                        expected_exit_ip=profile.expected_exit_ip,
                        status=PreflightStatus.INTERNAL_ERROR,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                results.append(result)
                self._update_profile_metrics(result)
                logger.info(
                    "VPN profile preflight completed profile=%r "
                    "status=%s expected_exit_ip=%s actual_exit_ip=%s "
                    "median_latency_ms=%s successful_attempts=%s "
                    "failed_attempts=%s error=%r",
                    result.profile_name,
                    result.status.value,
                    result.expected_exit_ip,
                    result.actual_exit_ip or "-",
                    result.median_latency_ms,
                    result.successful_attempts,
                    result.failed_attempts,
                    result.error,
                )

        return results

    def _check_profile(
        self,
        profile: VPNProfile,
        profile_dir: Path,
    ) -> PreflightResult:
        profile_dir.mkdir(parents=True, exist_ok=True)
        config_path = profile_dir / "runtime-config.json"
        access_log_path = profile_dir / "xray-access.log"
        error_log_path = profile_dir / "xray-error.log"
        stdout_path = profile_dir / "xray.stdout.log"
        stderr_path = profile_dir / "xray.stderr.log"

        result = PreflightResult(
            profile_name=profile.name,
            expected_exit_ip=profile.expected_exit_ip,
            status=PreflightStatus.INTERNAL_ERROR,
        )

        try:
            socks_port = get_free_port()
            runtime_config, outbound_tag = build_runtime_config(
                profile=profile,
                socks_port=socks_port,
                access_log_path=str(access_log_path),
                error_log_path=str(error_log_path),
            )
            result.outbound_tag = outbound_tag
            config_path.write_text(
                json.dumps(
                    runtime_config,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            xray = XrayProcess(
                binary=self.settings.xray_binary,
                config_path=config_path,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )
            xray.validate()

            with xray:
                xray.wait_for_port(
                    port=socks_port,
                    timeout_seconds=(
                        self.settings.xray_start_timeout_seconds
                    ),
                )
                samples, errors = self._collect_samples(socks_port)

            result.samples = samples
            result.successful_attempts = len(samples)
            result.failed_attempts = errors

            if len(samples) < self.settings.preflight_min_successes:
                result.status = PreflightStatus.EXIT_IP_UNAVAILABLE
                result.error = (
                    "Not enough successful proxy probes: "
                    f"{len(samples)}/{self.settings.preflight_attempts}"
                )
                return result

            ip_counts = Counter(sample.exit_ip for sample in samples)
            actual_exit_ip, occurrences = ip_counts.most_common(1)[0]
            if occurrences != len(samples):
                result.status = PreflightStatus.EXIT_IP_UNSTABLE
                result.actual_exit_ip = actual_exit_ip
                result.error = f"Different exit IPs were observed: {dict(ip_counts)}"
                return result

            latencies = [sample.latency_ms for sample in samples]
            result.actual_exit_ip = actual_exit_ip
            result.median_latency_ms = round(
                statistics.median(latencies), 3
            )
            result.jitter_ms = round(
                max(latencies) - min(latencies), 3
            )

            if actual_exit_ip != profile.expected_exit_ip:
                logger.warning(
                    "VPN profile exit IP changed profile=%r "
                    "configured_exit_ip=%s actual_exit_ip=%s",
                    profile.name,
                    profile.expected_exit_ip,
                    actual_exit_ip,
                )

            result.status = PreflightStatus.SUCCESS
            return result

        except (XrayConfigError, XrayConfigRejectedError) as exc:
            result.status = PreflightStatus.CONFIG_INVALID
            result.error = str(exc)
            return result
        except XrayStartError as exc:
            result.status = PreflightStatus.XRAY_START_FAILED
            result.error = str(exc)
            return result
        except ProxyProbeError as exc:
            result.status = PreflightStatus.PROXY_UNREACHABLE
            result.error = str(exc)
            return result
        except Exception as exc:
            result.status = PreflightStatus.INTERNAL_ERROR
            result.error = f"{type(exc).__name__}: {exc}"
            return result
        finally:
            try:
                config_path.unlink(missing_ok=True)
            except OSError:
                pass
            (profile_dir / "result.json").write_text(
                json.dumps(
                    result.to_dict(),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

    def _collect_samples(
        self,
        socks_port: int,
    ) -> tuple[list, int]:
        probe = ProxyProbe(
            urls=self.settings.probe_urls,
            timeout_seconds=self.settings.probe_timeout_seconds,
        )
        samples = []
        failures = 0
        last_error: ProxyProbeError | None = None

        for attempt in range(self.settings.preflight_attempts):
            try:
                samples.append(probe.run(socks_port=socks_port))
            except ProxyProbeError as exc:
                failures += 1
                last_error = exc
            if attempt + 1 < self.settings.preflight_attempts:
                time.sleep(0.2)

        if not samples and last_error is not None:
            raise last_error
        return samples, failures

    @staticmethod
    def _write_run_result(
        run_dir: Path,
        plan: PreflightPlan,
    ) -> None:
        (run_dir / "plan.json").write_text(
            json.dumps(
                plan.to_dict(),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _cleanup_old_runs(self) -> None:
        self.settings.runs_dir.mkdir(parents=True, exist_ok=True)
        directories = sorted(
            (
                item
                for item in self.settings.runs_dir.iterdir()
                if item.is_dir()
            ),
            key=lambda item: item.name,
            reverse=True,
        )
        for old_directory in directories[self.settings.retained_runs :]:
            shutil.rmtree(old_directory, ignore_errors=True)

    @staticmethod
    def _reset_latest_metrics() -> None:
        VPN_PROFILE_AVAILABLE.clear()
        VPN_PROFILE_MEDIAN_LATENCY_MILLISECONDS.clear()
        VPN_GROUP_AVAILABLE_PROFILES.clear()
        VPN_GROUP_SELECTED_PROFILES.clear()
        VPN_PLAN_READY.set(0)

    @staticmethod
    def _update_profile_metrics(result: PreflightResult) -> None:
        VPN_PROFILE_CHECKS_TOTAL.labels(
            profile=result.profile_name,
            expected_exit_ip=result.expected_exit_ip,
            result=result.status.value,
        ).inc()
        VPN_PROFILE_AVAILABLE.labels(
            profile=result.profile_name,
            expected_exit_ip=result.expected_exit_ip,
        ).set(1 if result.available else 0)

        if result.available and result.median_latency_ms is not None:
            VPN_PROFILE_MEDIAN_LATENCY_MILLISECONDS.labels(
                profile=result.profile_name,
                exit_ip=result.actual_exit_ip,
            ).set(result.median_latency_ms)

    @staticmethod
    def _update_plan_metrics(plan: PreflightPlan) -> None:
        VPN_PLAN_READY.set(1 if plan.groups else 0)
        parse_ready_at = datetime.fromisoformat(
            plan.parse_ready_at.replace("Z", "+00:00")
        )
        VPN_PARSE_READY_TIMESTAMP_SECONDS.set(parse_ready_at.timestamp())
        for group in plan.groups:
            VPN_GROUP_AVAILABLE_PROFILES.labels(
                exit_ip=group.exit_ip
            ).set(len(group.ranked_profiles))
            VPN_GROUP_SELECTED_PROFILES.labels(
                exit_ip=group.exit_ip
            ).set(len(group.selected_profiles))
