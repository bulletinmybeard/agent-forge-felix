# Runs, reports, and rollback

Every run is persisted under `~/.felix/runs/<timestamp>/` — a self-contained, auditable, secret-redacted record. The directory name (`YYYYMMDD-HHMMSS`) is the `RUN_ID` used by `last` / `explain` / `undo` / `replay`.

## Run-store layout

```
~/.felix/runs/20260604-142233/
  prompt.txt           the original prompt
  plan.md              investigation/plan preview (from "/api/dry-run")
  observations.jsonl   one probe/finding per line (phase = before | after | run)
  commands.jsonl       every command run or proposed, with its risk tier
  fix-plan.md           the proposed/applied fix plan
  report.md            the structured final report
  meta.json            run metadata (prompt, flags, agent, verdict, timing)
  skills.jsonl         which fleet skills were retrieved + injected
  events.jsonl         raw event trace what AgentForge actually sent
  changed-files/        pre-change snapshots (.orig) and remote diffs (.diff)
```

Everything written here passes through the redactor, and the tree is created `0o700` (owner-only) — see [safety.md](safety.md#secret-redaction). The `changed-files/` snapshots are what `felix undo` reverts to.

## The report

The `report.md` combines two halves (`felix/report.py`):

- The `@felix` agent's narrative — Summary, Current Status, Expected Goal, Evidence, Root Cause, and the Fix (applied or proposed). Felix cleans model output quirks (stray `|||` separators, repeated copies, conversational sign-offs) before storing it.
- The sections Felix owns from the run record — **Verification** (before vs after + verdict), **Risk Notes**, **Rollback Instructions**, **Commands Run** (each tagged `[tier/applied|proposed]`), and **Files Changed** (with `+adds -dels`).

`felix last` prints this report; `felix explain` surfaces the plan, root cause/evidence, the verdict, and any tool errors.

## How the verdict is decided

The agent self-reports a `VERDICT:` line, and it is usually trusted and has the real tool output verified (before/after) itself (`parse_verdict` tolerates markdown noise around the value). The client's verifier (`felix/verify/verifier.py`) then cross-checks it against its own before/after probe signals:

- `--dry-run` > `NOT APPLIED` (nothing executed).
- `--read-only` or nothing applied > `PROPOSED`.
- Otherwise the verifier compares before/after signals and decides:
  - improvements, no regressions > `FIXED`
  - improvements and regressions > `PARTIAL`
  - regressions only > `FAILED`
  - no measurable change either way > `FIXED` at edit level (the change was applied and verified at edit time, e.g., adding a timeout to a config / nothing regressed).

The signals are extracted from the probe text (HTTP status codes, container health, disk-used %, and error counts). Two deliberate design points:

- **Container health is judged by clearance, not a count drop.** A fix is `FIXED` only when the active failure is gone AND the target is positively seen healthy/running. A mere drop in the unhealthy count, or absence of the signal, is not treated as proof of recovery.
- **The verifier only matches real docker STATUS formats** (`Restarting (1)`, `(unhealthy)`, `.State` JSON) — never the bare words "restarting"/"unhealthy", which appear constantly in the agent's own grep filters and prose. Counting those fooled the verdict in both directions.

`contradicts_fixed` is the safety net against a stale-state FIXED claim: if the agent says FIXED but a re-probe *positively* shows the active failure persisting (or HTTP still failing), the verdict is downgraded. An inconclusive re-probe defers to the agent.

## Rollback

`code_edit` emits a `snapshot_id` in its `file.diff` events so Felix records it and snapshots the pre-change file into `changed-files/`.
To revert changes, run the undo command followed by the run directory reference (e.g., `20260604-163049`).

```bash
felix undo [RUN_ID]        # prompts first; --yes skips the prompt!
```

`undo` reverts what `revert_file` can roll back and lists anything needing a manual rollback (e.g., a service restart, a package install). `felix replay` re-traces the run's commands through `/api/dry-run` — it shows what each command *would* do without re-executing.
