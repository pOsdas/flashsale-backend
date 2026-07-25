

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vpn_controller.app.models import VPNProfile


DIRECT_PROTOCOLS = {
    "freedom",
    "blackhole",
    "dns",
    "loopback",
}


class ProfileConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SubscriptionProfile:
    name: str
    config: dict[str, Any]
    outbound_tag: str | None


@dataclass(frozen=True, slots=True)
class ProfileSelection:
    name: str
    expected_exit_ip: str
    enabled: bool = True
    occurrence: int = 1
    outbound_tag: str | None = None


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8-sig") as file:
            return json.load(file)
    except FileNotFoundError as exc:
        raise ProfileConfigurationError(
            f"Configuration file was not found: {path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ProfileConfigurationError(
            f"Invalid JSON in {path}: line={exc.lineno}, column={exc.colno}"
        ) from exc


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("\ufe0f", "")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.casefold()


def normalize_subscription_profiles(payload: Any) -> list[SubscriptionProfile]:
    if isinstance(payload, dict) and isinstance(payload.get("profiles"), list):
        entries = payload["profiles"]
    elif isinstance(payload, list):
        entries = payload
    elif isinstance(payload, dict) and isinstance(payload.get("outbounds"), list):
        entries = [payload]
    else:
        raise ProfileConfigurationError(
            "subscriptions.json must contain a profiles array, an array of "
            "Xray configs, or one Xray config with outbounds"
        )

    if not entries:
        raise ProfileConfigurationError(
            "subscriptions.json does not contain profiles"
        )

    result: list[SubscriptionProfile] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ProfileConfigurationError(
                f"Subscription profile #{index} must be an object"
            )

        wrapped = entry.get("config")
        if isinstance(wrapped, dict):
            config = wrapped
            name = (
                entry.get("name")
                or config.get("remarks")
                or (config.get("meta") or {}).get("serverDescription")
                or f"profile-{index}"
            )
            outbound_tag = entry.get("outbound_tag")
        else:
            config = entry
            name = (
                entry.get("name")
                or entry.get("remarks")
                or (entry.get("meta") or {}).get("serverDescription")
                or f"profile-{index}"
            )
            outbound_tag = entry.get("outbound_tag")

        outbounds = config.get("outbounds")
        if not isinstance(outbounds, list) or not outbounds:
            raise ProfileConfigurationError(
                f"Subscription profile #{index} has no outbounds"
            )

        result.append(
            SubscriptionProfile(
                name=str(name),
                config=config,
                outbound_tag=(
                    str(outbound_tag) if outbound_tag else None
                ),
            )
        )

    return result


def load_selections(payload: Any) -> list[ProfileSelection]:
    entries = payload.get("profiles") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ProfileConfigurationError(
            "vpn_profiles.json must contain a non-empty profiles array"
        )

    result: list[ProfileSelection] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ProfileConfigurationError(
                f"VPN profile selection #{index} must be an object"
            )

        name = str(entry.get("name") or "").strip()
        expected_exit_ip = str(
            entry.get("expected_exit_ip") or ""
        ).strip()
        if not name:
            raise ProfileConfigurationError(
                f"VPN profile selection #{index} has no name"
            )
        if not expected_exit_ip:
            raise ProfileConfigurationError(
                f"VPN profile selection {name!r} has no expected_exit_ip"
            )

        occurrence = int(entry.get("occurrence") or 1)
        if occurrence < 1:
            raise ProfileConfigurationError(
                f"VPN profile selection {name!r}: occurrence must be >= 1"
            )

        result.append(
            ProfileSelection(
                name=name,
                expected_exit_ip=expected_exit_ip,
                enabled=bool(entry.get("enabled", True)),
                occurrence=occurrence,
                outbound_tag=(
                    str(entry["outbound_tag"])
                    if entry.get("outbound_tag")
                    else None
                ),
            )
        )

    return result


def load_selected_profiles(
    subscriptions_path: Path,
    profiles_path: Path,
) -> list[VPNProfile]:
    subscriptions = normalize_subscription_profiles(
        load_json(subscriptions_path)
    )
    selections = load_selections(load_json(profiles_path))

    by_name: dict[str, list[SubscriptionProfile]] = {}
    for profile in subscriptions:
        by_name.setdefault(normalize_name(profile.name), []).append(profile)

    selected: list[VPNProfile] = []
    missing: list[str] = []

    for selection in selections:
        if not selection.enabled:
            continue

        matches = by_name.get(normalize_name(selection.name), [])
        if len(matches) < selection.occurrence:
            missing.append(
                f"{selection.name} (occurrence={selection.occurrence})"
            )
            continue

        source = matches[selection.occurrence - 1]
        selected.append(
            VPNProfile(
                name=source.name,
                expected_exit_ip=selection.expected_exit_ip,
                config=source.config,
                outbound_tag=(
                    selection.outbound_tag or source.outbound_tag
                ),
                occurrence=selection.occurrence,
            )
        )

    if missing:
        available = ", ".join(profile.name for profile in subscriptions)
        raise ProfileConfigurationError(
            "Configured VPN profiles were not found in subscriptions.json: "
            + "; ".join(missing)
            + f". Available profiles: {available}"
        )

    if not selected:
        raise ProfileConfigurationError(
            "No enabled VPN profiles were selected"
        )

    return selected
