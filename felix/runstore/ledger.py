"""Rollback ledger.

One record per change Felix made, appended to ledger.jsonl. File edits carry
the snapshot_id (== pre_hash) from the server's file.diff event so `felix undo`
can call revert_file(file_path, pre_hash) over the API. Command-style changes
(container recreate, prune, restart) store a manual rollback hint since they
have no clean inverse.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from felix.runstore.redact import redact, redact_obj

LEDGER_FILE = "ledger.jsonl"


@dataclass
class ChangeRecord:
    kind: str  # "file" | "command"
    reason: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    # file changes
    file_path: str | None = None
    pre_hash: str | None = None  # == snapshot_id; the revert_file handle
    post_hash: str | None = None
    diff_text: str | None = None
    action: str | None = None  # edited | written | reverted
    # command changes
    command: str | None = None
    tier: str | None = None
    rollback_hint: str | None = None
    # filled in after verification
    verification_result: str | None = None

    def to_record(self) -> dict[str, Any]:
        record = {k: v for k, v in asdict(self).items() if v is not None}
        if self.diff_text:
            record["diff_text"] = redact(self.diff_text)
        return redact_obj(record)  # type: ignore[return-value]


class Ledger:
    def __init__(self, run_path: Path) -> None:
        self.path = run_path / LEDGER_FILE

    def append(self, record: ChangeRecord) -> None:
        with self.path.open("a") as fh:
            fh.write(json.dumps(record.to_record(), default=str) + "\n")

    def stamp_verification(self, verdict: str) -> None:
        """Write the run verdict onto every record lacking one (rewrites file)."""
        records = self.read()
        for rec in records:
            rec.setdefault("verification_result", verdict)
        with self.path.open("w") as fh:
            for rec in records:
                fh.write(json.dumps(rec, default=str) + "\n")

    def read(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        out: list[dict[str, Any]] = []
        for line in self.path.read_text().splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def remove_by_paths(self, paths: set[str]) -> None:
        """Rewrite the ledger excluding file entries whose path is in ``paths``."""
        records = [r for r in self.read() if not (r.get("kind") == "file" and r.get("file_path") in paths)]
        with self.path.open("w") as fh:
            for rec in records:
                fh.write(json.dumps(rec, default=str) + "\n")

    def file_changes(self) -> list[dict[str, Any]]:
        """File records that carry a revertible pre_hash, newest first."""
        records = [r for r in self.read() if r.get("kind") == "file" and r.get("pre_hash")]
        return list(reversed(records))
