"""`felix undo last` — reverse a run's file changes via revert_file.

Walks the ledger newest-first and, for each revertible file change,
drives a server run that calls revert_file(file_path, pre_hash).
Non-file changes are reported as manual rollback hints (no clean inverse).
Revert is the safe direction, so confirms are auto-approved.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from felix.api.events import confirm_response_message, query_message
from felix.api.ws import WSClient
from felix.config import Config
from felix.runstore.reader import RunView


def _file_sha256(path: str) -> str | None:
    """sha256 of a locally-readable file (tools run on the same Mac), else None."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


@dataclass
class UndoOutcome:
    reverted: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)


def plan_undo(run: RunView) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a run's ledger into (revertible file changes, manual records).

    File changes are de-duplicated by (path, pre_hash) / the diff-preview flow
    emits a file.diff for both the propose card and the apply, so the same edit
    can appear twice.
    """
    seen: set[tuple] = set()
    file_changes: list[dict[str, Any]] = []
    for rec in run.ledger.file_changes():
        key = (rec.get("file_path"), rec.get("pre_hash"))
        if key in seen:
            continue
        seen.add(key)
        file_changes.append(rec)
    manual = [r for r in run.ledger.read() if r.get("kind") != "file" or not r.get("pre_hash")]
    return file_changes, manual


async def _revert_one(config: Config, agent: str, file_path: str, pre_hash: str) -> bool:
    """Drive a revert_file run, then confirm success by the file's hash.

    pre_hash is the sha256 of the original content, so a successful revert
    leaves the file hashing to pre_hash. That's definitive for local files and
    doesn't depend on a 'reverted' file.diff event (which cross-dispatched
    revert_file may not emit). Idempotent: already-reverted files short-circuit.
    """
    if _file_sha256(file_path) == pre_hash:
        return True

    text = (
        f"{agent} Undo the previous change to {file_path}: "
        f"call revert_file(file_path='{file_path}', pre_hash='{pre_hash}'). "
        f"Do nothing else."
    )
    ws = WSClient(config)
    event_ok = False
    try:
        await ws.connect()
        await ws.send(query_message(text, source=config.source, provider=config.provider))
        async for event in ws.events():
            if event.is_confirm_request and not event.get("auto_accepted"):
                await ws.send(confirm_response_message(event.get("request_id", ""), True))
            elif event.is_file_diff and event.get("action") == "reverted":
                event_ok = True
            elif event.type in {"agent.result", "agent.error", "agent.cancelled"}:
                break
    except Exception:  # noqa: BLE001
        event_ok = False
    finally:
        await ws.close()

    # Definitive check for local files; fall back to the event for remote ones.
    local = _file_sha256(file_path)
    return local == pre_hash if local is not None else event_ok


def undo_run(config: Config, run: RunView, agent: str) -> UndoOutcome:
    outcome = UndoOutcome()
    file_changes, manual = plan_undo(run)

    for rec in file_changes:
        path = rec.get("file_path", "")
        pre_hash = rec.get("pre_hash", "")
        if asyncio.run(_revert_one(config, agent, path, pre_hash)):
            outcome.reverted.append(path)
        else:
            outcome.failed.append(path)

    for rec in manual:
        hint = rec.get("rollback_hint") or rec.get("command") or rec.get("file_path") or "(unknown change)"
        outcome.manual.append(str(hint))

    return outcome
