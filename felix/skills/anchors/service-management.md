---
name: Service Management (systemd / launchd)
description: Diagnose and repair a down or flapping system service managed by systemd (Linux) or launchd (macOS) — status, logs, restart, config.
---

# Service management

## Probe order

1. Status: `systemctl status <unit>` (Linux) or `launchctl print <domain>/<label>` (macOS). Active? failed? last exit code?
2. Logs: `journalctl -u <unit> --no-pager -n 200` (Linux) or `log show --predicate ... --last 1h` (macOS). Read the failure timestamp.
3. Config: the unit file (`systemctl cat <unit>`) or the plist — wrong ExecStart, missing working dir, bad env, missing dependency (`After=`/`Requires=`).

## Common root causes

- Bad ExecStart path or missing binary/permissions.
- Missing/incorrect environment or working directory.
- Dependency not started (db/socket) -> service exits immediately and restarts.
- Port already in use by another process (`net_probe`).

## Fix + verify

Edit the unit/plist with `code_edit`, reload (`systemctl daemon-reload`), restart (a low-risk action), then re-check status and confirm it stays active with no new errors in the journal.
