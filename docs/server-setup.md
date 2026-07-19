# Server setup (AgentForge side)

Felix is a client. It needs a running [AgentForge](https://github.com/bulletinmybeard/agent-forge) **≥ [v0.13.0](https://github.com/bulletinmybeard/agent-forge/releases/tag/v0.13.0)** whose release includes Felix support — the `@felix` custom agent, the read-only gate, command permissions for shell/SSH (overrides + named profiles), and the no-clobber skill merge all ship baked into the AgentForge release stack. There is nothing to copy into the server from this repo.

## What the release provides

- **The `@felix` agent** — a `profile: agent` custom agent with the diagnostic / repair / research tool set and the Felix system prompt (scope check, diagnose read-only first, least-destructive fix, rollback tracking, one structured report + verdict).
- **Command permissions** (AgentForge ≥ 0.12 overrides; ≥ 0.13 profiles) — server-side allowlist / denylist / confirm for every `shell` / `ssh` call, before CommandGuard, plus named presets (`tight` / `open` / user). Critical for Felix because runs issue many shell/SSH diagnostics and repairs. See [safety.md](safety.md#agentforge-command-permissions-shell--ssh) and AgentForge `docs/SECURITY.md`.
- **The read-only gate** (`readonly_guard.py`) — refuses mutating tool calls, fail-closed, on a run that sends `overrides.read_only`. See [safety.md](safety.md#agentforge-read-only-gate).
- **The no-clobber skill merge** — a client-supplied `overrides._skills` is *merged with*, not overwritten by, the server's keyword resolver, so Felix's retrieved fleet skills inject reliably. See [skills.md](skills.md).

## Verify the wiring

After the AgentForge service is up, check the integration from the client:

```bash
felix doctor
```

It confirms the API is reachable, the `@felix` agent shows up in `/api/agents`, the SAQ worker is present in `/api/services`, the **command permissions** API is present (`/api/permissions/commands`, with effective shell/ssh modes), the skill-fleet search service is reachable, and the WebSocket connects. Run it first whenever something misbehaves.

## The `@felix` agent tools

For reference, the agent's tool set is split by purpose:

- **Read-only diagnostics** — `docker_ps`, `docker_logs`, `docker_inspect`, `docker_compose_status`, `docker_df`, `docker_images`, `docker_networks`, `docker_volumes`, `docker_cleanup_preview`, `system_overview`, `disk_usage`, `memory_info`, `process_list`, `http_check`, `health_check`, `dns_lookup`, `net_probe`, `analyze_logs`, `read_file`, `find_files`, `grep_text`.
- **Repair** (gated by command permissions + CommandGuard / confirmations) — `shell`, `ssh`, `write_file`, `code_edit` (config edits with diff preview + snapshot), `revert_file` (first-class rollback for `code_edit`).
- **Research** — `web_search`, `web_fetch`.

`code_edit` + `revert_file` give reversible config edits: `code_edit` emits a `snapshot_id` in its `file.diff` event, which Felix records, and `felix undo` replays through `revert_file`.

## Executor

AgentForge dispatches each tool call to the local **SAQ worker** (a launchd job on the MacBook), which is what actually runs the diagnostics and repairs on the local box. `felix doctor` looks for that worker in `/api/services`. See [architecture.md](architecture.md) for the full picture.
