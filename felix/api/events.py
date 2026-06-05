"""AgentForge-to-client WebSocket event model.

Mirrors AgentForge's web/server/protocol.py.
Rather than a class per event type (60+ of them),
we keep a thin typed wrapper that preserves the raw dict
and exposes the fields Felix actually consumes, plus a small set of helpers
to classify events into the categories the orchestrator/UI care about.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Event types that mutate the target system — Felix logs these to the ledger.
MUTATING_TOOL_ACTIONS = {"edited", "written", "reverted"}

# Terminal events that end (or abort) a run.
TERMINAL_TYPES = {"agent.result", "agent.error", "agent.cancelled"}

# Events carrying a tool invocation Felix records to commands.jsonl.
TOOL_CALL_TYPES = {"tool.call"}


@dataclass(frozen=True)
class Event:
    """A single decoded server event. ``raw`` holds the full payload."""

    type: str
    raw: dict[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    # -- classification helpers ------------------------------------------
    @property
    def is_terminal(self) -> bool:
        return self.type in TERMINAL_TYPES

    @property
    def is_confirm_request(self) -> bool:
        return self.type == "confirm.request"

    @property
    def is_secret_request(self) -> bool:
        return self.type == "secret.request"

    @property
    def is_tool_call(self) -> bool:
        return self.type in TOOL_CALL_TYPES

    @property
    def is_file_diff(self) -> bool:
        return self.type == "file.diff"

    @property
    def guard_threat(self) -> str | None:
        """Threat level the server's CommandGuard attached to a tool.call."""
        guard = self.raw.get("guard")
        if isinstance(guard, dict):
            return guard.get("threat")
        return None


def parse_event(payload: dict[str, Any]) -> Event:
    """Wrap a raw decoded JSON message into an Event."""
    return Event(type=str(payload.get("type", "")), raw=payload)


# -- outbound message constructors (client > server) --------------------


def query_message(
    text: str,
    *,
    session_id: str | None = None,
    source: str = "felix",
    overrides: dict[str, Any] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    incognito: bool = True,
    provider: str | None = None,
    read_only: bool = False,
) -> dict[str, Any]:
    # Felix runs are one-shot and independent: incognito skips cross-session
    # semantic memory storage AND recall, so a prior run's context (e.g., a
    # leftover file to revert) can never bleed into an unrelated new run.
    merged: dict[str, Any] = {"source": source, "incognito": incognito}
    if read_only:
        merged["read_only"] = True  # server refuses state-changing tools (read-only posture)
    if provider:
        merged["provider"] = provider  # remaps every tier to this provider's models
    if overrides:
        merged.update(overrides)
    msg: dict[str, Any] = {"type": "query", "text": text, "overrides": merged}
    if attachments:
        msg["attachments"] = attachments
    if session_id:
        msg["session_id"] = session_id
    return msg


def confirm_response_message(request_id: str, confirmed: bool, *, auto_accept: bool = False) -> dict[str, Any]:
    return {
        "type": "confirm.response",
        "request_id": request_id,
        "confirmed": confirmed,
        "auto_accept": auto_accept,
    }


def secret_response_message(request_id: str, value: str | None, cancelled: bool = False) -> dict[str, Any]:
    msg: dict[str, Any] = {"type": "secret.response", "request_id": request_id}
    if cancelled or value is None:
        msg["cancelled"] = True
    else:
        msg["value"] = value
    return msg


def cancel_message() -> dict[str, Any]:
    return {"type": "cancel"}


def ping_message() -> dict[str, Any]:
    return {"type": "ping"}
