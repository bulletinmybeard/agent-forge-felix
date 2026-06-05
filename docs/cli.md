# CLI reference

Felix is a [click](https://click.palletsclick.com/) group with a default command: any bare prompt routes to `run`.

```bash
felix "why is container xyz unhealthy?"      # -> felix run "..."
felix run "why is container xyz unhealthy?"  # explicit form
```

> Gotcha: a prompt that *starts* with `/` or `~` is parsed by the shell/click
> as an option-like token and trips group-usage instead of routing to `run`.
> Lead the prompt with a word ("check /var/log ...") or use the explicit `felix run` form.

## `run` — diagnose, fix, verify

```bash
felix run "<prompt>" [flags]
```

| Flag | Effect |
|:------|:--------|
| `--dry-run` | Trace the plan via `/api/dry-run`; execute nothing. |
| `--read-only` | Diagnose and propose only; block every state change. |
| `--apply` | Auto-apply read-only + low-risk fixes (no prompt for those tiers). |
| `--yes` | Auto-confirm medium-risk too (high-risk still prompts). |
| `--deep` | Deeper investigation — more areas/rounds. |
| `--discover` | Search skills.sh for task-relevant skills, pull + index them before the run. |

The flags compose to the most conservative outcome. See [safety.md](safety.md) for the full tier/flag decision table. Exit code `2` means the prompt failed the local preflight scope check (out of scope, or too vague to act on!).

## Prior-run commands

Each operates on the newest run by default, or on an explicit `RUN_ID` (the `<timestamp>` directory name). `last` is also accepted as an alias for "newest".

```bash
felix last [RUN_ID]       # print the run's report.md
felix explain [RUN_ID]    # plan + root cause/evidence + verdict + any errors
felix undo [RUN_ID]       # revert the run's file changes via revert_file
felix replay [RUN_ID]     # re-trace the run's commands through /api/dry-run
```

- `undo` prompts before reverting (`--yes` skips). It reverts files that `code_edit`/`revert_file` can roll back and lists anything that needs a manual rollback.
- `replay` is dry-run only and traces what each command *would* do, it does not re-execute.

See [runs-and-reports.md](runs-and-reports.md) for the run-store layout.

## `doctor` (self-check)

```bash
felix doctor
```

Checks, in order:

1. run-store writable
2. API health
3. `@felix` agent present in `/api/agents`
4. SAQ worker present in `/api/services`
5. Skill-fleet search service reachable
6. WebSocket connectivity
 
> Exits non-zero if any check fails. Run this first whenever something seems off/broken.

## `skills` (manage the skill fleet)

```bash
felix skills pull                           # acquire skills from sources.yaml into the catalog
felix skills index [--clean]                # chunk + index the catalog into Qdrant (incremental by default)
felix skills find "kubernetes crashloop"     # search the skills.sh registry
felix skills add owner/repo@skill [--index] # pull specific skill(s) into the catalog
felix skills discover "<query>"             # search skills.sh, pull matches, and index
felix skills retrieve "disk is full"        # preview which skills would be injected
felix skills list                           # show the acquired catalog + provenance
felix skills vet <path|owner/repo@skill>    # vet a skill without acquiring it
felix skills quarantine [NAME]              # list quarantined skills, or show one's findings
felix skills approve NAME [--index]         # release a quarantined skill into the catalog
```

`index --clean` does a full wipe+rebuild. The default is incremental. `add` and `approve` take `--index` to index the catalog right after.
See [skills.md](skills.md) for the full fleet model and the vetting checks.
