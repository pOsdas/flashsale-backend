#!/usr/bin/env python3


import argparse
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


def prometheus_query(base_url: str, expression: str) -> list[dict]:
    url = f"{base_url.rstrip('/')}/api/v1/query?{urlencode({'query': expression})}"
    with urlopen(url, timeout=15) as response:
        payload = json.load(response)
    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {payload}")
    return payload["data"]["result"]


def scalar(base_url: str, expression: str) -> float | None:
    result = prometheus_query(base_url, expression)
    if not result:
        return None
    try:
        return float(result[0]["value"][1])
    except (KeyError, TypeError, ValueError):
        return None


def k6_value(metrics: dict, metric: str, key: str) -> float | None:
    try:
        value = metrics[metric]["values"][key]
        return float(value)
    except (KeyError, TypeError, ValueError):
        return None


def fmt(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", required=True)
    parser.add_argument("--output", default="load_testing/results/load-report.md")
    parser.add_argument("--prometheus", default="http://127.0.0.1:9090")
    parser.add_argument("--window", default="1h")
    args = parser.parse_args()

    summary_path = Path(args.summary)
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    metrics = data.get("metrics", {})

    request_count = k6_value(metrics, "http_reqs", "count")
    failure_rate = k6_value(metrics, "http_req_failed", "rate")
    p95 = k6_value(metrics, "http_req_duration", "p(95)")
    p99 = k6_value(metrics, "http_req_duration", "p(99)")
    checks_rate = k6_value(metrics, "checks", "rate")
    dropped = k6_value(metrics, "dropped_iterations", "count") or 0

    prom_queries = {
        "Maximum active VUs": f"max_over_time(k6_vus[{args.window}])",
        "Maximum backend request rate": f"max_over_time((sum(rate(django_http_requests_total_by_method_total[1m])))[{args.window}:1m])",
        "Maximum due targets": f"max_over_time(monitoring_scanner_due_targets{{job=\"monitoring_scanner\"}}[{args.window}])",
        "Maximum overdue targets": f"max_over_time(monitoring_scanner_overdue_targets{{job=\"monitoring_scanner\"}}[{args.window}])",
        "Maximum outbox pending": f"max_over_time(outbox_pending_events{{job=\"outbox_worker\"}}[{args.window}])",
        "Maximum outbox failed": f"max_over_time(outbox_failed_events{{job=\"outbox_worker\"}}[{args.window}])",
        "Maximum RabbitMQ ready": f"max_over_time(rabbitmq_detailed_queue_messages_ready{{job=\"rabbitmq_detailed\",queue=\"flashsale.notifications\",vhost=\"/\"}}[{args.window}])",
        "Maximum notification DLQ": f"max_over_time(rabbitmq_detailed_queue_messages_ready{{job=\"rabbitmq_detailed\",queue=\"flashsale.notifications.dlq\",vhost=\"/\"}}[{args.window}])",
        "Maximum PostgreSQL connections": f"max_over_time((sum(pg_stat_activity_count{{datname=\"flashsale\"}}))[{args.window}:1m])",
        "Maximum Redis memory, bytes": f"max_over_time(redis_memory_used_bytes{{job=\"redis\"}}[{args.window}])",
    }
    prometheus_values: dict[str, float | None] = {}
    for name, query in prom_queries.items():
        try:
            prometheus_values[name] = scalar(args.prometheus, query)
        except Exception:
            prometheus_values[name] = None

    critical_telemetry = [
        "Maximum active VUs",
        "Maximum due targets",
        "Maximum outbox pending",
        "Maximum outbox failed",
        "Maximum notification DLQ",
        "Maximum PostgreSQL connections",
        "Maximum Redis memory, bytes",
    ]
    telemetry_complete = all(
        prometheus_values[name] is not None
        for name in critical_telemetry
    )
    checks: list[tuple[str, bool | None]] = [
        (
            "HTTP failure rate below 1%",
            None if failure_rate is None else failure_rate < 0.01,
        ),
        (
            "HTTP P95 below 750 ms",
            None if p95 is None else p95 < 750,
        ),
        (
            "HTTP P99 below 2000 ms",
            None if p99 is None else p99 < 2000,
        ),
        (
            "Checks above 99%",
            None if checks_rate is None else checks_rate > 0.99,
        ),
        ("No dropped iterations", dropped == 0),
        (
            "No notification DLQ messages",
            None
            if prometheus_values["Maximum notification DLQ"] is None
            else prometheus_values["Maximum notification DLQ"] == 0,
        ),
        (
            "No failed outbox accumulation",
            None
            if prometheus_values["Maximum outbox failed"] is None
            else prometheus_values["Maximum outbox failed"] == 0,
        ),
    ]
    passed = all(ok is True for _, ok in checks)
    has_unknown_checks = any(ok is None for _, ok in checks)
    verdict = (
        "INCOMPLETE"
        if not telemetry_complete or has_unknown_checks
        else ("PASS" if passed else "FAIL")
    )

    lines = [
        "# Flashsale Load Lab Report",
        "",
        f"**Verdict:** {verdict}",
        "",
        "## k6 result",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| HTTP requests | {fmt(request_count, 0)} |",
        f"| HTTP failure rate | {fmt(None if failure_rate is None else failure_rate * 100)}% |",
        f"| HTTP P95 | {fmt(p95)} ms |",
        f"| HTTP P99 | {fmt(p99)} ms |",
        f"| Checks | {fmt(None if checks_rate is None else checks_rate * 100)}% |",
        f"| Dropped iterations | {fmt(dropped, 0)} |",
        "",
        "## Platform maxima",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for name, value in prometheus_values.items():
        lines.append(f"| {name} | {fmt(value)} |")

    lines.extend(["", "## Acceptance checks", ""])
    if not telemetry_complete:
        lines.append("- ⚠️ Prometheus telemetry is incomplete; the result cannot be declared PASS.")
    for name, ok in checks:
        icon = "⚠️" if ok is None else ("✅" if ok else "❌")
        suffix = " (no data)" if ok is None else ""
        lines.append(f"- {icon} {name}{suffix}")

    lines.extend([
        "",
        "## Interpretation",
        "",
        "A PASS means the configured test profile met its SLOs on this exact hardware and configuration. "
        "It is not a universal guarantee for production. A FAIL should be correlated with the "
        "Flashsale Load Lab dashboard to identify whether the first bottleneck was API, PostgreSQL, "
        "Redis, scanner, outbox, RabbitMQ, notification delivery, or the load generator itself.",
        "",
    ])

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
