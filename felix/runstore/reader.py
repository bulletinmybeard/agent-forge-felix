"""Read a persisted run back — powers `felix last/explain/undo/replay`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from felix.runstore.ledger import Ledger
from felix.runstore.store import latest_run


@dataclass
class RunView:
    path: Path

    @property
    def run_id(self) -> str:
        return self.path.name

    def _read_text(self, name: str) -> str | None:
        p = self.path / name
        return p.read_text() if p.is_file() else None

    def _read_jsonl(self, name: str) -> list[dict[str, Any]]:
        p = self.path / name
        if not p.is_file():
            return []
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]

    @property
    def prompt(self) -> str | None:
        return self._read_text("prompt.txt")

    @property
    def plan(self) -> str | None:
        return self._read_text("plan.md")

    @property
    def fix_plan(self) -> str | None:
        return self._read_text("fix-plan.md")

    @property
    def report(self) -> str | None:
        return self._read_text("report.md")

    @property
    def meta(self) -> dict[str, Any]:
        raw = self._read_text("meta.json")
        return json.loads(raw) if raw else {}

    @property
    def observations(self) -> list[dict[str, Any]]:
        return self._read_jsonl("observations.jsonl")

    @property
    def commands(self) -> list[dict[str, Any]]:
        return self._read_jsonl("commands.jsonl")

    @property
    def ledger(self) -> Ledger:
        return Ledger(self.path)


def load_run(root: Path, run_id: str | None = None) -> RunView | None:
    """Load a run by id, or the latest run when run_id is None."""
    if run_id:
        path = root / run_id
        return RunView(path) if path.is_dir() else None
    latest = latest_run(root)
    return RunView(latest) if latest else None
