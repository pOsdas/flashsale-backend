
from collections import defaultdict

from vpn_controller.app.models import (
    PreflightResult,
    ProfileGroupPlan,
)


def profile_rank_key(result: PreflightResult) -> tuple:
    return (
        result.failed_attempts,
        result.median_latency_ms
        if result.median_latency_ms is not None
        else float("inf"),
        result.jitter_ms
        if result.jitter_ms is not None
        else float("inf"),
        result.profile_name.casefold(),
    )


def build_group_plan(
    results: list[PreflightResult],
    max_profiles_per_group: int,
) -> tuple[list[ProfileGroupPlan], list[PreflightResult]]:
    grouped: dict[str, list[PreflightResult]] = defaultdict(list)
    unavailable: list[PreflightResult] = []

    for result in results:
        if result.available and result.actual_exit_ip:
            grouped[result.actual_exit_ip].append(result)
        else:
            unavailable.append(result)

    groups: list[ProfileGroupPlan] = []
    for exit_ip, profiles in grouped.items():
        ranked = sorted(profiles, key=profile_rank_key)
        groups.append(
            ProfileGroupPlan(
                exit_ip=exit_ip,
                ranked_profiles=ranked,
                selected_profiles=ranked[:max_profiles_per_group],
            )
        )

    groups.sort(
        key=lambda group: profile_rank_key(group.ranked_profiles[0])
    )
    unavailable.sort(key=lambda item: item.profile_name.casefold())
    return groups, unavailable
