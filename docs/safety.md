# Safety model

Felix can change live system state, so safety is layered. No single mechanism is trusted on its own:

1. **Risk tiers** classify every action (client-side).
2. **The confirm policy** decides approve / prompt / deny from the tier + mode flags (client-side).
3. **AgentForge read-only gate** independently refuses mutating tools on a read-only run (AgentForge-side, fail-closed).
4. **Egress gating** stops a diagnostic probe being used as an exfiltration channel.
5. **Secret redaction** scrubs everything written to the run-store.
6. **The skill firewall** vets acquired skills before they can influence a run.

## Risk tiers

Every action maps to one of four tiers (`felix/safety/tiers.py`). Classification uses AgentForge's `guard.threat` signal, the tool name, the command text, and the confirm-prompt text. Destructive signals always win over the read-only allowlist, so a read-named tool can never downgrade a dangerous command.

| Tier | Examples | Default handling |
|------|----------|------------------|
| `READ_ONLY` | `docker_ps`, `disk_usage`, `read_file`, `http_check` to a local/internal host | auto-approve |
| `LOW` | `docker restart`, `systemctl reload`, prune of dangling images/networks, `rm` of `/tmp` or `*.log/.tmp/.cache` | confirm (auto with `--apply`) |
| `MEDIUM` | unknown mutating tool, sudo ops, any server confirm prompt, a network probe to a non-local/non-allowlisted host | confirm (auto with `--yes`) |
| `HIGH` | `rm -rf` on broad paths, `docker volume rm`, `system prune --volumes`, `drop database`, firewall/credential/package changes, `mkfs`, `dd if=` | blocked; needs `--yes` AND an explicit per-step confirm |

## Confirm policy

The four mode flags compose to the most conservative outcome (`felix/safety/gate.py`):

| Tier | (no flags) | `--apply` | `--yes` | `--read-only` |
|------|-----------|-----------|---------|---------------|
| READ_ONLY | approve | approve | approve | approve |
| LOW | prompt | approve | approve | deny |
| MEDIUM | prompt | prompt | approve* | deny |
| HIGH | deny | deny | prompt | deny |

`*` Approving MEDIUM under `--yes` sets `auto_accept` so AgentForge stops prompting for the rest of the run. **HIGH is never auto-approved** — even with `--yes` it still requires one explicit interactive confirm, so Felix never runs a destructive op fully unattended. `--dry-run` is handled upstream: no execution reaches the gate at all.

## AgentForge read-only gate

`--read-only` is also enforced AgentForge-side, independently of the client policy.
The client sends `overrides.read_only` and AgentForge's `readonly_guard.py` refuses any tool call that could change state, failing **closed**. A call is allowed only when it is provably read-only:

- Known structured writers (`code_edit`, `write_file`, `apply_patch`, ...) are always blocked.
- `shell` / `ssh` commands are parsed segment by segment: every segment must lead with a read-only verb (or a read-only subcommand of a dual-use tool like `docker` / `git` / `systemctl` / `kubectl`), with no output redirection to a real file.
- Anything not recognized as read-only is treated as blocked.

So a read-only run is propose-only end to end: the agent diagnoses, the report carries a PROPOSED fix, and nothing is applied even if the client policy were bypassed.

## Egress gating

The outbound diagnostic tools (`http_check`, `dns_lookup`, `net_probe`) are read-only against the thing being diagnosed, but a probe to an *arbitrary external* host is an data leakage channel (smuggled in a URL query, a DNS subdomain, ...). So:

- Local / loopback / private-IP / internal-name targets (`localhost`, `10.x`, bare hostnames, `*.local` / `*.internal` / k8s service suffixes) are always allowed.
- A non-local, non-allowlisted external host escalates the probe to MEDIUM, so it can't ride the auto-approve of the read-only tier.

Add the public hosts you legitimately diagnose to `egress_allow_hosts` (see [configuration.md](configuration.md#egress)).

## Secret redaction

Everything written to the run-store passes through a best-effort redactor (`felix/runstore/redact.py`): the prompt, plan, fix-plan, report, meta, and every JSONL line and diff.
The run tree is also created owner-only (`0o700`) because regex redaction can miss secrets. The `@felix` agent is additionally instructed never to read or collect secrets (`~/.ssh`, `~/.aws`, `.env`, tokens, keys) and never to send system data to an unrelated host.

## Skill firewall

An acquired skill's body consists of instructions the agent will follow, so it is untrusted input. Felix vets every skill on the way into the catalog (`Qdrant collection`) with two gates:

- **Gate 1 — static scan** (always on): flags dangerous patterns and categories of finding, assigns a risk level.
- **Gate 2 — LLM judge** (only if `vetting_provider` is set): a second opinion on anything the static scan didn't already hard-reject. Felix calls the provider directly, independent of agent-forge.

`skills_vetting` controls the outcome: `quarantine` (default) holds failing skills in `~/.felix/quarantine/` for review, `warn` logs the verdict but
catalogs the skill anyway, `off` disables vetting. Inspect and release quarantined skills with `felix skills quarantine` / `felix skills approve` (see [cli.md](cli.md#skills--manage-the-skill-fleet)).

The agent is also told, at the prompt level, to treat injected skills, tool output, and fetched web content as DATA, never as instructions. So a malicious skill that says "ignore your rules" is not obeyed.
