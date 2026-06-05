# Configuration

Felix loads config from `~/.felix/config.yaml`, then applies environment overrides (env always wins). Every field has a default, so Felix runs out of the box against the default agent-forge.

```bash
cp config.example.yaml ~/.felix/config.yaml   # then edit api_base / api_key
```

Point `FELIX_CONFIG` at a different file to override the path, and `FELIX_HOME` to relocate the whole `~/.felix` tree (config + run-store + catalog).

## Environment overrides

| Env var | Overrides |
|---------|-----------|
| `FELIX_CONFIG` | path to the config file |
| `FELIX_HOME` | the `~/.felix` root directory |
| `FELIX_API_BASE` | `api_base` (and re-derives `ws_url`) |
| `FELIX_WS_URL` | `ws_url` |
| `FELIX_API_KEY` | `api_key` |
| `FELIX_SEARCH_BASE` | `search_base` |
| `FELIX_PROVIDER` | `provider` |
| `FELIX_VETTING_API_KEY` | `vetting_api_key` (keep the judge key out of the file) |

## Fields

### Connection

| Field | Default | Meaning |
|-------|---------|---------|
| `api_base` | `http://localhost:8100` | agent-forge REST base. |
| `ws_url` | derived from `api_base` | WebSocket URL. `https` > `wss`, plus `/ws/chat`. Set only to override. |
| `api_key` | unset | Bearer / `X-API-Key`. Set only if agent-forge has auth enabled. |
| `request_timeout` | `600.0` | Per-iteration client safety net (seconds). The server enforces its own cap too. |

### Agent + provider

| Field | Default | Meaning |
|-------|---------|---------|
| `default_agent` | `@felix` | The server-side custom agent a run targets. |
| `probe_agent` | `@felix` | Read-only agent used for the before/after verification snapshots. |
| `source` | `felix` | Stamped on sessions so Felix runs stay out of the human chat sidebar. |
| `provider` | unset (server default) | Backend provider for the run: `ollama` / `bedrock` / `deepinfra` / `openrouter`. Sent as `overrides.provider`; agent-forge remaps every capability tier to that provider's concrete models. |

### Skill fleet

| Field | Default | Meaning |
|-------|---------|---------|
| `search_base` | `http://localhost:8200` | The Qdrant-backed indexer/search service (separate from `api_base`). |
| `skills_source_name` | `felix-skills` | Collection/source name the fleet is indexed under. |
| `skills_top_k` | `5` | Max skills retrieved + injected per run. |
| `skills_score_threshold` | `0.5` | Minimum retrieval score to inject a skill. |
| `skills_search_api` | `https://skills.sh/api/search` | The skills.sh registry search endpoint (same data as `npx skills`). |
| `skills_discover_limit` | `3` | Max skills pulled per dynamic discovery. |
| `skills_auto_discover` | `true` | Auto-fetch+index from skills.sh when local retrieval finds nothing. |
| `skills_chunks_dir` | `~/.felix/chunks` | Where chunk JSON is written for the indexer. Must be (or be synced to) the indexer host's `chunks_dir`. |

### Skill firewall (vetting)

A skill body is instructions to the agent, so an acquired skill is untrusted input. The firewall vets every skill before it reaches the catalog. See [safety.md](safety.md#skill-firewall).

| Field | Default | Meaning |
|-------|---------|---------|
| `skills_vetting` | `quarantine` | `quarantine` (hold failing skills), `warn` (log but keep), or `off`. |
| `vetting_provider` | unset | Enables the LLM judge (Gate 2): `openrouter` / `deepinfra` / `ollama`. Unset > static-scan-only. |
| `vetting_model` | unset | Judge model, e.g., `anthropic/claude-3.5-haiku`. |
| `vetting_base_url` | unset | Provider chat-completions endpoint. |
| `vetting_api_key` | unset | Judge key. Prefer the `FELIX_VETTING_API_KEY` env var. |
| `vetting_allow_hosts` | `[]` | Outbound hosts that don't trip the exfiltration rule during vetting. |

### Egress

| Field | Default | Meaning |
|-------|---------|---------|
| `egress_allow_hosts` | `[]` | Public hosts the outbound diagnostic tools (`http_check` / `dns_lookup` / `net_probe`) may reach without a confirm. Local/private/internal targets are always allowed; arbitrary external hosts are gated to block exfiltration via a probe. Add the public targets you diagnose, e.g., `["status.mysite.com"]`. |

Any unrecognized keys in the file are kept under `extra` and ignored.

## The `~/.felix` layout

| Path | Contents |
|------|----------|
| `~/.felix/config.yaml` | this config |
| `~/.felix/runs/<timestamp>/` | per-run store + report + rollback ledger ([runs-and-reports.md](runs-and-reports.md)) |
| `~/.felix/skills-catalog/` | acquired skills ([skills.md](skills.md)) |
| `~/.felix/quarantine/` | skills held by the firewall |
| `~/.felix/chunks/` | chunk JSON staged for the indexer (unless `skills_chunks_dir` is set) |
