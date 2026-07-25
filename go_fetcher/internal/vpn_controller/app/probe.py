

import ipaddress
import subprocess
import time
from dataclasses import dataclass

from vpn_controller.app.models import ProbeSample


class ProxyProbeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProxyProbe:
    urls: tuple[str, ...]
    timeout_seconds: float

    def run(self, socks_port: int) -> ProbeSample:
        errors: list[str] = []
        for url in self.urls:
            try:
                return self._run_url(socks_port=socks_port, url=url)
            except ProxyProbeError as exc:
                errors.append(f"{url}: {exc}")

        raise ProxyProbeError("; ".join(errors))

    def _run_url(self, socks_port: int, url: str) -> ProbeSample:
        command = [
            "curl",
            "--silent",
            "--show-error",
            "--fail",
            "--location",
            "--proxy",
            f"socks5h://127.0.0.1:{socks_port}",
            "--connect-timeout",
            str(min(self.timeout_seconds, 5.0)),
            "--max-time",
            str(self.timeout_seconds),
            "--write-out",
            "\n%{time_total}",
            url,
        ]
        started_at = time.monotonic()
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_seconds + 2,
            check=False,
        )
        elapsed_ms = (time.monotonic() - started_at) * 1000

        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "").strip()
            raise ProxyProbeError(
                f"curl exit={completed.returncode}: {message[-1000:]}"
            )

        lines = [line.strip() for line in completed.stdout.splitlines()]
        lines = [line for line in lines if line]
        if not lines:
            raise ProxyProbeError("empty response")

        exit_ip = lines[0]
        try:
            ipaddress.ip_address(exit_ip)
        except ValueError as exc:
            raise ProxyProbeError(
                f"response is not an IP address: {exit_ip!r}"
            ) from exc

        latency_ms = elapsed_ms
        if len(lines) >= 2:
            try:
                latency_ms = float(lines[-1]) * 1000
            except ValueError:
                pass

        return ProbeSample(
            latency_ms=round(latency_ms, 3),
            exit_ip=exit_ip,
            probe_url=url,
        )
