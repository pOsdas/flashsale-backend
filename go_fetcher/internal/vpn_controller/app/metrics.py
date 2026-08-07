from prometheus_client import Counter, Gauge, Histogram


VPN_CONTROLLER_HEARTBEAT_TIMESTAMP_SECONDS = Gauge(
    "vpn_controller_heartbeat_timestamp_seconds",
    "Unix timestamp of the VPN controller scheduler heartbeat",
)

VPN_PREFLIGHT_RUNNING = Gauge(
    "vpn_preflight_running",
    "Whether a VPN preflight run is currently executing",
)

VPN_PREFLIGHT_RUNS_TOTAL = Counter(
    "vpn_preflight_runs_total",
    "Total VPN preflight runs",
    ["result"],
)

VPN_PREFLIGHT_DURATION_SECONDS = Histogram(
    "vpn_preflight_duration_seconds",
    "VPN preflight run duration in seconds",
)

VPN_PROFILE_CHECKS_TOTAL = Counter(
    "vpn_profile_checks_total",
    "Total VPN profile preflight checks",
    ["profile", "expected_exit_ip", "result"],
)

VPN_PROFILE_AVAILABLE = Gauge(
    "vpn_profile_available",
    "Whether a configured VPN profile passed the latest preflight",
    ["profile", "expected_exit_ip"],
)

VPN_PROFILE_MEDIAN_LATENCY_MILLISECONDS = Gauge(
    "vpn_profile_median_latency_milliseconds",
    "Median proxy probe latency from the latest successful preflight",
    ["profile", "exit_ip"],
)

VPN_GROUP_AVAILABLE_PROFILES = Gauge(
    "vpn_group_available_profiles",
    "Number of available VPN profiles in an exit-IP group",
    ["exit_ip"],
)

VPN_GROUP_SELECTED_PROFILES = Gauge(
    "vpn_group_selected_profiles",
    "Number of profiles selected for parsing in an exit-IP group",
    ["exit_ip"],
)

VPN_PLAN_READY = Gauge(
    "vpn_plan_ready",
    "Whether at least one VPN profile is available in the latest plan",
)

VPN_PARSE_READY_TIMESTAMP_SECONDS = Gauge(
    "vpn_parse_ready_timestamp_seconds",
    "Unix timestamp when parsing may start for the latest cycle",
)

VPN_PREFLIGHT_LAST_SUCCESS_TIMESTAMP_SECONDS = Gauge(
    "vpn_preflight_last_success_timestamp_seconds",
    "Unix timestamp of the latest preflight with at least one profile",
)

VPN_PARSE_REQUESTS_TOTAL = Counter(
    "vpn_parse_requests_total",
    "Total marketplace parse requests handled by the VPN gateway",
    ["marketplace", "result"],
)

VPN_PARSE_REQUEST_DURATION_SECONDS = Histogram(
    "vpn_parse_request_duration_seconds",
    "VPN gateway parse request duration in seconds",
    ["marketplace", "result"],
)

VPN_PARSE_ATTEMPTS_TOTAL = Counter(
    "vpn_parse_attempts_total",
    "Total marketplace parse attempts through VPN profiles",
    ["marketplace", "exit_ip", "profile", "result"],
)

VPN_GROUP_PARSE_EXHAUSTED_TOTAL = Counter(
    "vpn_group_parse_exhausted_total",
    "Total exhausted VPN IP groups during marketplace parsing",
    ["marketplace", "exit_ip", "reason"],
)

VPN_GROUP_SUSPECTED_REJECTION = Gauge(
    "vpn_group_suspected_rejection",
    "Whether the latest cycle has a suspected marketplace rejection for an IP group",
    ["marketplace", "exit_ip"],
)

VPN_ACTIVE_SESSION = Gauge(
    "vpn_active_session",
    "Whether a VPN profile is currently active for marketplace parsing",
    ["exit_ip", "profile"],
)

VPN_ACTIVE_SESSION_LAST_USED_TIMESTAMP_SECONDS = Gauge(
    "vpn_active_session_last_used_timestamp_seconds",
    "Unix timestamp when the active VPN parsing session was last used",
)

VPN_MARKETPLACE_AUTH_FAILURES_TOTAL = Counter(
    "vpn_marketplace_auth_failures_total",
    "Marketplace authentication failures detected by the VPN gateway",
    ["marketplace"],
)

VPN_MARKETPLACE_AUTH_FAILURES_TOTAL.labels(
    marketplace="ozon",
)
VPN_MARKETPLACE_AUTH_FAILURES_TOTAL.labels(
    marketplace="wb",
)
