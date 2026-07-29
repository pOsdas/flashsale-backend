
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class PreflightStatus(StrEnum):
    SUCCESS = "success"
    CONFIG_INVALID = "config_invalid"
    XRAY_START_FAILED = "xray_start_failed"
    PROXY_UNREACHABLE = "proxy_unreachable"
    EXIT_IP_UNAVAILABLE = "exit_ip_unavailable"
    EXIT_IP_UNSTABLE = "exit_ip_unstable"
    EXIT_IP_MISMATCH = "exit_ip_mismatch"
    INTERNAL_ERROR = "internal_error"


class ParseAttemptStatus(StrEnum):
    SUCCESS = "success"
    PROFILE_NOT_FOUND = "profile_not_found"
    CONFIG_INVALID = "config_invalid"
    XRAY_START_FAILED = "xray_start_failed"
    EXIT_IP_UNAVAILABLE = "exit_ip_unavailable"
    EXIT_IP_MISMATCH = "exit_ip_mismatch"
    WORKER_UNAVAILABLE = "worker_unavailable"
    REQUEST_INVALID = "request_invalid"
    MARKETPLACE_NOT_FOUND = "marketplace_not_found"
    MARKETPLACE_TIMEOUT = "marketplace_timeout"
    MARKETPLACE_CONNECTION_ERROR = "marketplace_connection_error"
    MARKETPLACE_REJECTED = "marketplace_rejected"
    PARSER_ERROR = "parser_error"
    INVALID_RESPONSE = "invalid_response"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class VPNProfile:
    name: str
    expected_exit_ip: str
    config: dict[str, Any]
    outbound_tag: str | None = None
    occurrence: int = 1


@dataclass(frozen=True, slots=True)
class ProbeSample:
    latency_ms: float
    exit_ip: str
    probe_url: str


@dataclass(slots=True)
class PreflightResult:
    profile_name: str
    expected_exit_ip: str
    status: PreflightStatus
    outbound_tag: str = ""
    actual_exit_ip: str = ""
    median_latency_ms: float | None = None
    jitter_ms: float | None = None
    successful_attempts: int = 0
    failed_attempts: int = 0
    error: str = ""
    samples: list[ProbeSample] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.status == PreflightStatus.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["available"] = self.available
        return data


@dataclass(slots=True)
class ProfileGroupPlan:
    exit_ip: str
    ranked_profiles: list[PreflightResult]
    selected_profiles: list[PreflightResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_ip": self.exit_ip,
            "available_profiles_count": len(self.ranked_profiles),
            "selected_profiles_count": len(self.selected_profiles),
            "ranked_profiles": [
                item.to_dict() for item in self.ranked_profiles
            ],
            "selected_profiles": [
                item.to_dict() for item in self.selected_profiles
            ],
        }


@dataclass(slots=True)
class PreflightPlan:
    cycle_id: str
    cycle_started_at: str
    completed_at: str
    parse_ready_at: str
    next_preflight_at: str
    groups: list[ProfileGroupPlan]
    unavailable_profiles: list[PreflightResult]

    @property
    def available_profiles_count(self) -> int:
        return sum(len(group.ranked_profiles) for group in self.groups)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "cycle_started_at": self.cycle_started_at,
            "completed_at": self.completed_at,
            "parse_ready_at": self.parse_ready_at,
            "next_preflight_at": self.next_preflight_at,
            "available_profiles_count": self.available_profiles_count,
            "unavailable_profiles_count": len(self.unavailable_profiles),
            "groups": [group.to_dict() for group in self.groups],
            "unavailable_profiles": [
                item.to_dict() for item in self.unavailable_profiles
            ],
        }


@dataclass(slots=True)
class ParseAttemptResult:
    marketplace: str
    cycle_id: str
    group_exit_ip: str
    profile_name: str
    status: ParseAttemptStatus
    confirmed_exit_ip: str = ""
    worker_status_code: int = 0
    duration_ms: float = 0.0
    error: str = ""

    @property
    def successful(self) -> bool:
        return self.status == ParseAttemptStatus.SUCCESS

    @property
    def confirmed_marketplace_attempt(self) -> bool:
        return bool(self.confirmed_exit_ip and self.worker_status_code)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["successful"] = self.successful
        data["confirmed_marketplace_attempt"] = (
            self.confirmed_marketplace_attempt
        )
        return data
