# Changelog

All notable changes to Felix are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-07-19

Requires [AgentForge](https://github.com/bulletinmybeard/agent-forge) **≥ 0.13.0** for command permission profiles (`/api/permissions/profiles/*`). Command-permissions overrides (`/api/permissions/commands/*`) ship from AgentForge 0.12.

### Added

- **Verbosity** on `felix run`: `-v` / `-vv` (and group-level `felix -vv …`) for richer terminal output without changing investigation depth
- `-vv` tool **output previews** (needs AgentForge with `output` on `agent.tool_exec`); mouse CSI scrub + disable tracking; plan tools line uses custom-agent tool list / truncation
- `felix permissions` group to manage AgentForge runtime shell/SSH overrides:
  `show`, `set-mode`, `allow`, `allow-pattern`, `deny-pattern`, `remove`, `reset`, `check`
- `felix permissions profile` — list/show/apply/save/delete named presets (`tight` / `open` / user), including synthetic `__yaml__` / `__blank__` apply ids
- `RestClient` helpers: `command_permissions`, `get_command_overrides`, `put_command_overrides`,
  `delete_command_overrides`, `validate_command`, plus profile list/get/apply/save/delete
- `felix doctor` checks AgentForge **command permissions** (`GET /api/permissions/commands`) and prints effective shell/ssh modes
- Docs: safety / server-setup / architecture / CLI / README cover command permissions, profiles, and the AgentForge **0.13.0+** requirement

### Fixed

- False **Fixed** verdict when only diagnostics ran (activation regex matched path
  substrings like `Service Worker`; read-only auto-confirms no longer set `applied_any`)

## [0.2.1] - 2026-06-18

### Fixed

- Confirmation gate: denied file edits are now reverted on disk via `revert_file` instead of staying applied
- Confirmation gate: denying a prompt now cancels the entire run immediately so the agent cannot retry the same change via a different tool (e.g., `sed -i`, `write_file`)
- Duplicate `file.diff` events from the server's two-phase code_edit flow no longer create weird pending entries
- `applied_any` is no longer set prematurely on `file.diff` receipt. Deferred to the confirm gate or auto-flush so a denied edit produces a `Proposed` verdict instead of `Fixed`

### Added

- `_flush_pending_files()` / `_revert_pending_files()` helpers for the buffered confirmation flow
- `Ledger.remove_by_paths()` to clean up ledger entries for denied file changes

## [0.2.0] - 2026-06-18

First tagged release.

### Added

- skills.sh attribution links in skill commands (`skills list`, `skills find`, `skills add`)
- `Added` column in `skills find` results to which skills are already in the catalog
- [ChalkBox](https://github.com/bulletinmybeard/chalkbox) tables for `skills list`, `skills find`, and `skills add` output

### Changed

- Bump minimum Python version from 3.11 to 3.12
- Bump version to 0.2.0 for the first official GitHub release

## [0.1.0] - 2026-06-06

Initial public release.

### Added

- Autonomous diagnostic-repair agent CLI (`felix "..."`)
- AgentForge WebSocket integration (`/ws/chat`) with live event stream rendering
- Risk-tiered safety model: read-only (auto), low (`--apply`), medium (confirm), high (`--yes` + confirm)
- Subcommands: `doctor`, `last`, `explain`, `undo`, `replay`
- Skill fleet with semantic retrieval from Qdrant
  - Bundled anchor skills (`felix/skills/anchors/*.md`)
  - GitHub source acquisition via `felix/skills/sources.yaml`
  - skills.sh dynamic discovery (`felix skills find`, `felix skills add`, `--discover`)
  - Skill pull, index, retrieve, list commands
- Run-store with rollback ledger and reports (`~/.felix/runs/<timestamp>/`)
- Before/after verification for applied fixes
- Confirm/secret prompt gates for tool actions with side effects
- Modes: `--dry-run`, `--read-only`, `--apply`, `--yes`, `--deep`
- Configuration via `~/.felix/config.yaml` with env overrides
- CI workflow (lint, test, build + wheel verification)
- Documentation: architecture, CLI reference, configuration, safety model, skill fleet, server setup, runs/reports
