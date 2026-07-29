import copy
import ipaddress
import socket
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


def _is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False

    return True


def _resolve_ipv4(hostname: str, port: int) -> str:
    try:
        addresses = socket.getaddrinfo(
            hostname,
            port,
            family=socket.AF_INET,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as error:
        raise XrayConfigError(
            f"Failed to resolve VPN server hostname {hostname!r}: {error}"
        ) from error

    resolved_addresses = [
        address_info[4][0]
        for address_info in addresses
        if address_info[4]
    ]

    if not resolved_addresses:
        raise XrayConfigError(
            f"VPN server hostname {hostname!r} has no IPv4 addresses"
        )

    return resolved_addresses[0]


def _replace_server_hostname_with_ipv4(
    server: dict[str, Any],
) -> None:
    address = server.get("address")
    port = server.get("port")

    if not isinstance(address, str) or not address:
        return

    if _is_ip_address(address):
        return

    if not isinstance(port, int):
        try:
            port = int(port)
        except (TypeError, ValueError) as error:
            raise XrayConfigError(
                f"VPN server {address!r} has invalid port {port!r}"
            ) from error

    server["address"] = _resolve_ipv4(address, port)


def _resolve_outbound_server_addresses(
    outbounds: list[dict[str, Any]],
) -> None:
    for outbound in outbounds:
        settings = outbound.get("settings")
        if not isinstance(settings, dict):
            continue

        # VLESS и VMess.
        vnext = settings.get("vnext")
        if isinstance(vnext, list):
            for server in vnext:
                if isinstance(server, dict):
                    _replace_server_hostname_with_ipv4(server)

        # Hysteria2, Trojan, Shadowsocks, SOCKS и другие конфигурации,
        # где серверы располагаются в settings.servers.
        servers = settings.get("servers")
        if isinstance(servers, list):
            for server in servers:
                if isinstance(server, dict):
                    _replace_server_hostname_with_ipv4(server)


def build_runtime_config(
    profile: VPNProfile,
    socks_port: int,
    access_log_path: str,
    error_log_path: str,
    listen_host: str = "127.0.0.1",
) -> tuple[dict[str, Any], str]:
    source = profile.config
    outbounds = source.get("outbounds")
    if not isinstance(outbounds, list) or not outbounds:
        raise XrayConfigError("Profile has no outbounds")

    selected_tag, copied_outbounds = select_outbound(
        outbounds,
        profile.outbound_tag,
    )

    # Домены VPN-серверов разрешаются системным DNS контейнера до запуска
    # Xray. REALITY serverName при этом не изменяется.
    _resolve_outbound_server_addresses(copied_outbounds)

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
                "listen": listen_host,
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

    # dns намеренно не переносится. В исходном профиле DNS-запросы
    # маршрутизировались через ещё не поднятый proxy и создавали рекурсию.
    for key in (
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
