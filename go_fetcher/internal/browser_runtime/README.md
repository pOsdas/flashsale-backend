# Shared Google Chrome CDP runtime

Both marketplace browser workers use this package.

The runtime:

1. receives a SOCKS proxy and VPN session ID from `vpn_controller`;
2. refuses to start without a proxy when `BROWSER_REQUIRE_PROXY=true`;
3. creates a session-specific non-default Chrome profile directory;
4. starts Xvfb for headed Chrome inside the container;
5. starts Google Chrome Stable with `--remote-debugging-port` and the VPN proxy;
6. waits for `/json/version` to expose a DevTools WebSocket URL;
7. connects Playwright with `connect_over_cdp()`;
8. reuses Chrome while the VPN session is unchanged;
9. closes Chrome, Xvfb and Playwright when the VPN session changes;
10. removes old profile directories and runtime logs according to retention limits.

The CDP port is bound to `127.0.0.1` inside each browser container and is not published by Docker.

Production must keep these settings:

```env
BROWSER_REQUIRE_PROXY=true
BROWSER_PROXY_SERVER=
OZON_BROWSER_CDP_URL=
WB_BROWSER_CDP_URL=
```

This makes a missing dynamic proxy fail closed instead of silently starting a direct browser session.
