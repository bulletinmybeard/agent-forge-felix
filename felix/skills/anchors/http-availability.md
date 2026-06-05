---
name: HTTP/API Availability Debugging
description: Debug a failing HTTP endpoint or API — distinguish DNS failure, connection refused, 5xx from the app, and reverse-proxy misroutes.
---

# HTTP/API availability debugging

## Probe order

1. `http_check` — status code or timeout. Note 4xx vs 5xx vs no-connection.
2. `dns_lookup` — does the host resolve? Wrong/missing record?
3. `net_probe` — is the port open and is a process bound to it locally?
4. Container/upstream `docker_logs` + `analyze_logs` — what the app says at the failure timestamp.

## Distinguish the failure mode

- DNS failure: name does not resolve -> fix the record or use the right host.
- Connection refused: nothing listening on the port -> service down or wrong port/bind address.
- 5xx from the app: the app is reachable but erroring -> read its logs.
- Proxy misroute: reverse proxy returns 502/504 -> upstream is down or the proxy points at the wrong target.

## Fix + verify

Apply the narrowest fix (bind address, port mapping, upstream target, restart), then re-run `http_check` and confirm a 2xx/3xx and no new errors in the logs.
