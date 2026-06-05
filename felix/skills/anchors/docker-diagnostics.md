---
name: Docker Container Diagnostics
description: Diagnose unhealthy, restarting, or unreachable Docker containers and compose services — health, ports, env, logs, crash loops.
---

# Docker container & compose diagnostics

## Probe order

1. `docker_ps` — is the container running? health status? restart count climbing (crash loop)?
2. `docker_logs` (tail) — recent errors, stack traces, "connection refused", the timestamp around failure.
3. `docker_inspect` — exposed/published ports, env vars, mounts, restart policy.
4. `docker_compose_status` — service health and dependency ordering.
5. `http_check` against the published port to confirm the symptom.

## Common root causes

- Port mismatch: app listens on port X inside the container, but compose publishes Y:Z that doesn't map to X.
- Wrong env: a service points at `localhost` instead of the compose service name (e.g., `REDIS_HOST=localhost` should be `redis`).
- Unhealthy dependency: the container is up but a dependency (db, cache) is unreachable, so health checks fail.
- Crash loop: bad config or a missing file makes the container exit and restart.

## Fix + verify

Fix at the source of truth (compose / env / Dockerfile) with `code_edit`, reversible via `revert_file`.

ACTIVATE the change — an edited file is inert until the running container is rebuilt/recreated from it:

- Changed a Dockerfile or build context: `docker compose up -d --build <svc>` (or `docker build` then `docker compose up -d --force-recreate <svc>`). `docker compose restart` does NOT pick up a rebuilt image — it restarts the existing container on its existing image, so the fix never takes.
- Changed compose/env only (no rebuild): `docker compose up -d <svc>` recreates the container with the new config; `restart` keeps the old config.
- If the project ships a deploy script (e.g., `deploy-*.sh`), prefer it — it encodes the correct activation.

Then PROVE the running container carries the change before verifying: confirm its image id matches the freshly built image, or that the changed file is present *inside the running container* — not just on disk. Re-run `docker_ps` + `docker_logs` + `http_check` and compare to the before state. A still-`Restarting` container means activation did not take.
