

import copy
from typing import Any

from vpn_controller.app.models import VPNProfile
from vpn_controller.app.profile_loader import DIRECT_PROTOCOLS


class XrayConfigError(ValueError):
    pass


def select_outbound(
    outbounds: list[dict[str, Any]],
    requested_tag: str | None,
) -> tuple[str, list[dict[str, Any]]]:
    copied = copy.deepcopy(outbounds)
    tags: dict[str, dict[str, Any]] = {}

    for index, outbound in enumerate(copied):
        if not isinstance(outbound, dict):
            raise XrayConfigError(f"outbounds[{index}] must be an object")

        tag = str(outbound.get("tag") or f"generated-outbound-{index}")
        outbound["tag"] = tag
        tags[tag] = outbound

    if requested_tag:
        if requested_tag not in tags:
            raise XrayConfigError(
                f"outbound_tag={requested_tag!r} was not found; "
                f"available={sorted(tags)}"
            )
        return requested_tag, copied

    if "proxy" in tags:
        return "proxy", copied

    for outbound in copied:
        protocol = str(outbound.get("protocol") or "").lower()
        if protocol not in DIRECT_PROTOCOLS:
            return str(outbound["tag"]), copied

    raise XrayConfigError(
        "VPN outbound was not found; set outbound_tag for the profile"
    )


def build_runtime_config(
    profile: VPNProfile,
    socks_port: int,
    access_log_path: str,
    error_log_path: str,
) -> tuple[dict[str, Any], str]:
    source = profile.config
    outbounds = source.get("outbounds")
    if not isinstance(outbounds, list) or not outbounds:
        raise XrayConfigError("Profile has no outbounds")

    selected_tag, copied_outbounds = select_outbound(
        outbounds,
        profile.outbound_tag,
    )

    source_routing = source.get("routing")
    domain_strategy = "IPIfNonMatch"
    if isinstance(source_routing, dict):
        domain_strategy = str(
            source_routing.get("domainStrategy") or domain_strategy
        )

    runtime: dict[str, Any] = {
        "log": {
            "loglevel": "warning",
            "access": access_log_path,
            "error": error_log_path,
        },
        "inbounds": [
            {
                "tag": "vpn-preflight-socks",
                "listen": "127.0.0.1",
                "port": socks_port,
                "protocol": "socks",
                "settings": {
                    "auth": "noauth",
                    "udp": True,
                },
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls", "quic"],
                    "routeOnly": True,
                },
            }
        ],
        "outbounds": copied_outbounds,
        "routing": {
            "domainStrategy": domain_strategy,
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["vpn-preflight-socks"],
                    "outboundTag": selected_tag,
                }
            ],
        },
    }

    source_log = source.get("log")
    if isinstance(source_log, dict) and source_log.get("loglevel"):
        runtime["log"]["loglevel"] = source_log["loglevel"]

    for key in (
        "dns",
        "policy",
        "stats",
        "transport",
        "reverse",
        "observatory",
        "burstObservatory",
        "fakeDns",
    ):
        if key in source:
            runtime[key] = copy.deepcopy(source[key])

    if isinstance(source_routing, dict):
        for key in ("balancers", "domainMatcher"):
            if key in source_routing:
                runtime["routing"][key] = copy.deepcopy(
                    source_routing[key]
                )

    return runtime, selected_tag
