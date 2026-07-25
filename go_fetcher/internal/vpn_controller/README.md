# VPN controller: stage 1

This service performs a lightweight production preflight for the explicitly
selected VPN subscriptions. It does not contact Ozon or Wildberries.

For every profile it:

1. Builds an isolated Xray config with a local SOCKS inbound.
2. Validates and starts Xray.
3. Sends three small IP-check requests through SOCKS.
4. Confirms the real exit IP.
5. Calculates median latency and jitter.
6. Ranks all available profiles inside each exit-IP group.
7. Selects at most three profiles per group for the future parsing stage.

The service keeps no Xray process after preflight. Parser traffic is not routed
through it yet; that is the next integration stage.

## Configuration

The selected ten profiles and their expected exit IPs are versioned in:

`go_fetcher/internal/vpn_controller/vpn_profiles.json`

The full Happ export contains credentials and must be stored only at:

`go_fetcher/secrets/subscriptions.json`

The secrets directory is mounted read-only by Docker Compose and ignored by Git.

## API

- `GET /api/v1/health`
- `GET /api/v1/preflight/latest`
- `POST /api/v1/preflight/run`
- `GET /metrics`

## Cycle

By default preflight starts immediately and then every 3600 seconds. The plan
contains `parse_ready_at`, which is exactly 600 seconds after the cycle start.
The scheduler uses a fixed monotonic schedule, so preflight duration does not
accumulate drift.


Runtime Xray configs are deleted after each profile check. The state volume keeps
only probe results and diagnostic logs, not subscription JSON or generated configs.
