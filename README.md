# Felix

[![CI](https://github.com/bulletinmybeard/agent-forge-felix/actions/workflows/ci.yml/badge.svg)](https://github.com/bulletinmybeard/agent-forge-felix/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Poetry](https://img.shields.io/endpoint?url=https://python-poetry.org/badge/v0.json)](https://python-poetry.org/)
[![Requires AgentForge](https://img.shields.io/badge/requires-AgentForge-blueviolet)](https://github.com/bulletinmybeard/agent-forge)

<!-- prettier-ignore -->
> [!NOTE]
> **Experimental — a personal learning project.**
> Felix is a sandbox for one question: can an agent take an ambiguous "X is broken" prompt and work the whole loop itself, trace the root cause, apply a fix behind a safety gate, and prove it actually worked? Expect rough edges, model-dependent behavior, thin test coverage, and breaking changes.

Autonomous local diagnostic-repair agent. **diagnose. fix. verify.**

Inspired by Fix-It Felix (*Wreck-It Ralph*) — "I can fix it!", for your systems.

```bash
felix "find out why the docker container xyz-1 is not responding and make it work again"
felix "my VM is running out of disk space. Free up some safely"
felix "why does localhost:3000 not respond?"
felix doctor
```

Felix diagnoses a broken thing, finds the root cause from evidence, proposes a fix, applies it behind a safety gate (with confirmation prompts), verifies the result before/after, and saves a rollback-aware report. It targets **any system-level problem**. Flexibility comes from a retrieved fleet of Agent Skills (see below), not hardcoded domains.

## Architecture

Felix is a thin client over [AgentForge](https://github.com/bulletinmybeard/agent-forge):

- **Brain**: the remote AgentForge web service runs the agent loop, classification, investigation/synthesis, and the safety guard.
- **Executor**: AgentForge dispatches tool execution to a local SAQ worker (launchd) on the MacBook. That is why Felix can diagnose the local box.
- **Felix client**: drives a run over the `/ws/chat` WebSocket, renders the live event stream, enforces a risk-tiered confirm policy, runs the mandatory before/after verification, and persists a local run-store + rollback ledger + report under `~/.felix/runs/<timestamp>/`.

## Modes

| Flag | Effect |
|:------|:--------|
| `--dry-run` | Trace the plan via `/api/dry-run`; execute nothing. |
| `--read-only` | Diagnose and propose only; block every change. |
| `--apply` | Auto-apply read-only + low-risk fixes. |
| `--yes` | Auto-confirm medium-risk (high-risk still prompts). |
| `--deep` | Deeper investigation. |

Safety tiers:

1. read-only (auto)
2. low (auto with `--apply`)
3. medium (confirm)
4. high (blocked by default and needs `--yes` + an explicit confirm).

## Subcommands

```bash
felix doctor              # check API, WebSocket, SAQ worker, @felix agent
felix last                # show the latest run's report
felix explain [RUN_ID]    # root cause + evidence
felix undo [RUN_ID]       # revert file changes via revert_file
felix replay [RUN_ID]     # replay commands through the dry-run pipeline
```

## Skill fleet

Felix retrieves the most relevant Agent Skills for each problem and injects them into the run. Skills (open `SKILL.md` format) are acquired from GitHub / [skills.sh](https://www.skills.sh/), indexed into AgentForge's Qdrant vector database via its existing chunker + indexer, and retrieved semantically at runtime.

```bash
felix skills pull                       # fetch skills from felix/skills/sources.yaml -> catalog
felix skills index                      # chunk + index the catalog into Qdrant
felix skills retrieve "disk is full"    # preview which skills would be injected
felix skills list                       # show the acquired catalog + provenance
```

The catalog always includes the bundled system-ops **anchor skills** (`felix/skills/anchors/*.md`) so Felix has core diagnostic know-how regardless of external sources. Add curated GitHub repos via `felix/skills/sources.yaml`.

### Dynamic discovery (skills.sh)

Search the [skills.sh](https://www.skills.sh/) registry and pull specific skills on demand (same data as `npx skills`, via its JSON API):

```bash
felix skills find "kubernetes crashloop"          # search the registry
felix skills add owner/repo@skill --index        # pull a specific skill + index it
felix "why is pod X crashlooping?" --discover    # auto-search + pull + index before the run
```

Retrieval hits the indexer/search service (`search_base`), separate from the chat service (`api_base`). Indexing writes chunk JSON to `skills_chunks_dir`, which must be readable by the indexer host.

## Setup

```bash
poetry install
cp config.example.yaml ~/.felix/config.yaml   # edit api_base / api_key
poetry run felix doctor
```

Requires a running AgentForge stack that includes the `@felix` agent (baked into the release). See [docs/server-setup.md](docs/server-setup.md). Run `felix doctor` to verify connectivity and local settings.

## Documentation

Documentation and References live in [`docs/`](docs/):

- [Architecture](docs/architecture.md): the three tiers, run lifecycle, event stream.
- [CLI reference](docs/cli.md): every command and flag.
- [Configuration](docs/configuration.md): all config fields, env overrides, `~/.felix` layout.
- [Safety model](docs/safety.md): risk tiers, confirm policy, read-only gate, egress gating, redaction, skill firewall.
- [Skill fleet](docs/skills.md): anchors, sources, skills.sh discovery, indexing, retrieval, vetting.
- [Server setup](docs/server-setup.md): wiring `@felix` into AgentForge.
- [Runs & reports](docs/runs-and-reports.md): run-store layout, report format, verdicts, undo/replay.

## License

MIT, see [LICENSE](LICENSE).
