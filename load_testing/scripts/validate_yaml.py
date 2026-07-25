

import sys
from pathlib import Path

import yaml


def collect_yaml_files(root: Path) -> list[Path]:
    files = [
        root / "docker-compose.load.yml",
        root / "docker-compose.integration.yml",
        root / "load_testing" / "prometheus" / "prometheus.load.yml",
    ]

    provisioning_root = root / "load_testing" / "grafana" / "provisioning"
    files.extend(sorted(provisioning_root.rglob("*.yml")))
    files.extend(sorted(provisioning_root.rglob("*.yaml")))

    # Preserve deterministic output and avoid duplicates.
    return list(dict.fromkeys(files))


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: validate_yaml.py <repository-root>", file=sys.stderr)
        return 2

    root = Path(sys.argv[1]).resolve()
    files = collect_yaml_files(root)

    missing = [path for path in files[:3] if not path.is_file()]
    if missing:
        for path in missing:
            print(f"Missing required YAML file: {path}", file=sys.stderr)
        return 1

    if len(files) == 3:
        print(
            f"No Grafana provisioning YAML files found under "
            f"{root / 'load_testing' / 'grafana' / 'provisioning'}",
            file=sys.stderr,
        )
        return 1

    failed = False
    for path in files:
        try:
            with path.open("r", encoding="utf-8") as file:
                yaml.safe_load(file)
        except Exception as exc:  # noqa: BLE001 - report exact YAML/parser failure.
            failed = True
            print(f"Invalid YAML: {path}", file=sys.stderr)
            print(f"  {exc}", file=sys.stderr)

    if failed:
        return 1

    print(f"YAML OK: {len(files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
