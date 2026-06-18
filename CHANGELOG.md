# Changelog

All notable changes to Felix are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
