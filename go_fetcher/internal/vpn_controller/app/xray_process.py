
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import TextIO


class XrayConfigRejectedError(RuntimeError):
    pass


class XrayStartError(RuntimeError):
    pass


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class XrayProcess:
    def __init__(
        self,
        binary: Path,
        config_path: Path,
        stdout_path: Path,
        stderr_path: Path,
    ) -> None:
        self.binary = binary
        self.config_path = config_path
        self.stdout_path = stdout_path
        self.stderr_path = stderr_path
        self.process: subprocess.Popen[str] | None = None
        self._stdout_file: TextIO | None = None
        self._stderr_file: TextIO | None = None

    def validate(self, timeout_seconds: float = 15.0) -> None:
        command = [
            str(self.binary),
            "run",
            "-test",
            "-config",
            str(self.config_path),
        ]
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            output = (completed.stdout or "").strip()
            raise XrayConfigRejectedError(
                f"Xray rejected config: {output[-2000:]}"
            )

    def start(self) -> None:
        if not self.binary.is_file():
            raise XrayStartError(
                f"Xray binary was not found: {self.binary}"
            )

        try:
            self._stdout_file = self.stdout_path.open(
                "w", encoding="utf-8"
            )
            self._stderr_file = self.stderr_path.open(
                "w", encoding="utf-8"
            )

            self.process = subprocess.Popen(
                [
                    str(self.binary),
                    "run",
                    "-config",
                    str(self.config_path),
                ],
                stdout=self._stdout_file,
                stderr=self._stderr_file,
                text=True,
                start_new_session=True,
            )
        except Exception as exc:
            self.stop()
            raise XrayStartError(
                f"Failed to start Xray: {type(exc).__name__}: {exc}"
            ) from exc

    def wait_for_port(
        self,
        port: int,
        timeout_seconds: float,
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            process = self.process
            if process is None:
                raise XrayStartError("Xray process was not started")

            exit_code = process.poll()
            if exit_code is not None:
                raise XrayStartError(
                    f"Xray exited before opening SOCKS port; code={exit_code}"
                )

            try:
                with socket.create_connection(
                    ("127.0.0.1", port), timeout=0.5
                ):
                    return
            except OSError:
                time.sleep(0.2)

        raise XrayStartError(
            f"Xray did not open 127.0.0.1:{port} "
            f"within {timeout_seconds:.1f} seconds"
        )


    def is_running(self) -> bool:
        return bool(
            self.process is not None
            and self.process.poll() is None
        )

    def stop(self) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=4)
            except Exception:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=4)
                except Exception:
                    pass

        self.process = None
        for stream_name in ("_stdout_file", "_stderr_file"):
            stream = getattr(self, stream_name)
            if stream is not None:
                try:
                    stream.close()
                finally:
                    setattr(self, stream_name, None)

    def __enter__(self) -> "XrayProcess":
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()
