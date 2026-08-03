# VPN controller

The service has two responsibilities:

1. Run an hourly preflight over the configured VPN profiles without requests to Ozon or Wildberries.
2. Provide a marketplace gateway that performs browser parsing through the ranked VPN plan.

## Cycle

```text
preflight over all configured profiles
→ wait 10 minutes
→ allow marketplace parsing through the ranked plan
→ next preflight 60 minutes after the previous cycle start
```

All nine profiles with exit IP `88.216.223.105` are checked. Only the three best currently available profiles from this group are placed into `selected_profiles`. The `175.110.122.57` group contains its single configured profile.

Parsing is blocked while preflight is running and during the first ten minutes of a new cycle. The existing monitoring scanner may submit sequential requests during the remaining parse window.

## Gateway endpoints

The gateway mirrors the existing browser-worker contracts:

```text
POST /api/v1/product   Ozon product
POST /api/v1/search    Ozon search
POST /api/v1/category  Ozon category
POST /api/v1/fetch     Wildberries browser fetch
```

Additional controller endpoints:

```text
GET  /api/v1/health
GET  /api/v1/preflight/latest
POST /api/v1/preflight/run
GET  /api/v1/parse/runtime
GET  /metrics
```

The gateway starts one Xray process for the selected profile and reuses it for subsequent sequential requests. A new clean browser session is created whenever the VPN profile changes. The active Xray process is also stopped before every new preflight cycle and after the configured idle period.

Both browser workers use lazy startup. Health checks validate configuration without starting a browser or opening a marketplace. After the controller activates and confirms a VPN profile, the worker starts Google Chrome Stable as a separate process with a private `--user-data-dir`, a local `--remote-debugging-port`, and the controller-provided SOCKS proxy. Playwright then attaches through `connect_over_cdp()` instead of launching bundled Chromium. The CDP port is available only inside the browser container and is not published by Docker.

A separate browser profile directory is used for every VPN session. This avoids carrying marketplace state across profile switches, while the current successful session is reused for sequential requests. Old profile directories and Chrome/Xvfb logs are retained only up to configured limits.

## Retry and isolation rules

Failures are separated by scope:

- Xray/configuration/exit-IP failures disable the profile for both marketplaces until the next preflight.
- Ozon rejection, timeout or connection failure disables the profile only for Ozon in the current cycle.
- Wildberries rejection, timeout or connection failure disables the profile only for Wildberries in the current cycle.
- Generic parser errors are retried through the remaining selected profiles for the current request, but they do not permanently disable the profile or IP group.
- Invalid input, product-not-found responses and browser-worker unavailability stop the request immediately because changing VPN cannot fix them.

A successful profile remains active and is prioritized for the next sequential request, avoiding unnecessary Xray and Google Chrome restarts.

## Group failure classification

A suspected marketplace rejection is emitted only when at least two different profiles:

- started successfully;
- confirmed the expected exit IP;
- received a marketplace-rejection result for the same marketplace.

Startup failures, exit-IP failures, timeouts and connection errors are tracked separately and do not by themselves mean that the IP is blocked.

Grafana has two VPN alerts:

- cautious manual-check alert for a suspected marketplace rejection;
- route/group-unavailable alert for infrastructure or mixed persistent failures.

Neither alert claims that an IP is definitely blocked.

## Timeouts

The controller limits one Ozon browser attempt to 75 seconds and waits at most 90 seconds for one browser-worker HTTP response. This keeps sequential fallback bounded while still leaving enough time for the existing browser parsing flow.

## Production routing and temporary unavailability

`docker-compose.prod.yml` overrides the Go parser settings and enables required-browser mode:

```env
WB_BROWSER_FETCHER_URL=http://vpn_controller:8097
WB_BROWSER_FETCHER_ENABLED=true
WB_BROWSER_FETCHER_REQUIRED=true
OZON_BROWSER_FETCHER_URL=http://vpn_controller:8097
OZON_BROWSER_FETCHER_ENABLED=true
OZON_BROWSER_FETCHER_REQUIRED=true
```

Required-browser mode prevents the Go parser from sending a direct Ozon or Wildberries request before using the VPN gateway. Product, search and category operations all go through the controller.

During preflight, during the ten-minute waiting window, or while another parse request owns the gateway lock, the controller returns a temporary-unavailability response with `Retry-After`. The Go service preserves that state as HTTP 503. The Django monitoring scanner then postpones the target by the requested interval without creating a `PARSE_ERROR` snapshot and without moving the target to `FAILED`.

The timeout chain is intentionally ordered:

```text
one browser attempt: 75 seconds
controller worker request: 90 seconds
go_fetcher browser client: 420 seconds
go_fetcher HTTP write timeout: 430 seconds
Django go_fetcher client: 450 seconds
```

## Readiness endpoint

`GET /api/v1/readiness` reports whether the controller can accept a new
integration parsing run.

A `200` response means that:

- the preflight scheduler is alive;
- no preflight is running;
- a preflight plan exists;
- at least one VPN profile is available;
- the parse delay has elapsed;
- no parse session is active.

When the controller is not ready, it returns `503`, a machine-readable
`reason`, `retry_after_seconds`, and the matching `Retry-After` header.
