"""Run-store writer.

Lays out ~/.felix/runs/<timestamp>/ exactly as the spec requires:

    prompt.txt           the original user prompt
    plan.md              investigation/plan preview (from /api/dry-run)
    observations.jsonl   one probe/finding per line (phase=before|after|run)
    commands.jsonl       every command Felix ran or would run, with tier
    fix-plan.md          the proposed/applied fix plan
    report.md            the structured final report
    meta.json            run metadata (prompt, flags, agent, verdict, timing)
    changed-files/       pre-change snapshots of edited files
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from felix.runstore.redact import redact, redact_obj


def _new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


class RunStore:
    def __init__(self, root: Path, run_id: str | None = None) -> None:
        self.run_id = run_id or _new_run_id()
        self.root = root
        self.path = root / self.run_id
        self.changed_files = self.path / "changed-files"

    def create(self) -> RunStore:
        self.changed_files.mkdir(parents=True, exist_ok=True)
        # Runs can hold redaction-missed secrets (regex is best-effort); keep the
        # tree owner-only. Best-effort — odd filesystems may not support chmod.
        for p in (self.root, self.path):
            try:
                p.chmod(0o700)
            except OSError:
                pass
        return self

    # -- single-shot writers --------------------------------------------
    # report/plan/meta are agent-derived and can echo secrets the agent surfaced
    # (env dumps, config). The JSONL/diff writers already redact; route these
    # through redact() too so nothing skips the scrub on the way to disk.
    def write_prompt(self, text: str) -> None:
        (self.path / "prompt.txt").write_text(redact(text) + "\n")

    def write_plan(self, markdown: str) -> None:
        (self.path / "plan.md").write_text(redact(markdown))

    def write_fix_plan(self, markdown: str) -> None:
        (self.path / "fix-plan.md").write_text(redact(markdown))

    def write_report(self, markdown: str) -> None:
        (self.path / "report.md").write_text(redact(markdown))

    def write_meta(self, meta: dict[str, Any]) -> None:
        (self.path / "meta.json").write_text(json.dumps(redact_obj(meta), indent=2, default=str))

    # -- append-only logs ------------------------------------------------
    def append_observation(self, record: dict[str, Any]) -> None:
        self._append("observations.jsonl", record)

    def append_command(self, record: dict[str, Any]) -> None:
        self._append("commands.jsonl", record)

    def append_skill(self, record: dict[str, Any]) -> None:
        self._append("skills.jsonl", record)

    def append_event(self, record: dict[str, Any]) -> None:
        """Raw event-type trace — diagnostics for what the server actually sends."""
        self._append("events.jsonl", record)

    def _append(self, filename: str, record: dict[str, Any]) -> None:
        line = json.dumps(redact_obj(record), default=str)
        with (self.path / filename).open("a") as fh:
            fh.write(line + "\n")

    # -- file snapshots --------------------------------------------------
    def snapshot_file(self, source: Path) -> Path | None:
        """Copy an about-to-be-changed file into changed-files/ (redacted name)."""
        if not source.exists():
            return None
        safe = str(source).strip("/").replace("/", "__")
        dest = self.changed_files / f"{safe}.orig"
        shutil.copy2(source, dest)
        return dest

    def write_diff_snapshot(self, path_str: str, diff_text: str) -> None:
        """Persist a file.diff's unified diff (used when we can't read the original directly

        because the file lives on the remote host).
        """
        safe = path_str.strip("/").replace("/", "__")
        (self.changed_files / f"{safe}.diff").write_text(redact(diff_text))


def latest_run(root: Path) -> Path | None:
    """Newest run directory by name (timestamp-sortable), or None."""
    if not root.is_dir():
        return None
    runs = sorted((p for p in root.iterdir() if p.is_dir()), reverse=True)
    return runs[0] if runs else None
