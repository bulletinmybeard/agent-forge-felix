"""Client-side helpers for AgentForge command permission overrides.

Policy is owned by AgentForge (YAML baseline + SQLite runtime overrides).
Felix only GET/PUT/DELETE via REST. Mutators use get → merge → put so list
edits do not wipe the rest of the override document.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

ToolName = Literal["shell", "ssh"]
PolicyMode = Literal["confirm", "allowlist", "denylist"]

_EMPTY_POLICY: dict[str, Any] = {
    "mode": "confirm",
    "allowed_commands": [],
    "allowed_patterns": [],
    "blocked_patterns": [],
}

_LIST_KEYS = ("allowed_commands", "allowed_patterns", "blocked_patterns")


def empty_policy() -> dict[str, Any]:
    return deepcopy(_EMPTY_POLICY)


def normalize_policy(data: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce API/null payloads into a full policy dict."""
    base = empty_policy()
    if not data:
        return base
    mode = data.get("mode") or "confirm"
    if mode not in ("confirm", "allowlist", "denylist"):
        mode = "confirm"
    base["mode"] = mode
    for key in _LIST_KEYS:
        raw = data.get(key) or []
        if isinstance(raw, str):
            raw = [raw]
        base[key] = [str(x) for x in raw if str(x).strip()]
    return base


def merge_list_edit(
    policy: dict[str, Any],
    *,
    add: dict[str, list[str]] | None = None,
    remove: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Return a new policy with list membership updates (deduped, order preserved)."""
    out = normalize_policy(policy)
    add = add or {}
    remove = remove or {}
    for key in _LIST_KEYS:
        items = list(out[key])
        for val in remove.get(key) or []:
            items = [x for x in items if x != val]
        for val in add.get(key) or []:
            if val and val not in items:
                items.append(val)
        out[key] = items
    return out


def set_mode(policy: dict[str, Any], mode: PolicyMode) -> dict[str, Any]:
    out = normalize_policy(policy)
    out["mode"] = mode
    return out


def overrides_put_body(tool: ToolName, policy: dict[str, Any]) -> dict[str, Any]:
    """Body for PUT /api/permissions/commands/overrides (one tool)."""
    return {tool: normalize_policy(policy)}


def format_policy_block(title: str, policy: dict[str, Any] | None) -> str:
    p = normalize_policy(policy)
    lines = [
        f"{title}",
        f"  mode: {p['mode']}",
        f"  allowed_commands: {p['allowed_commands'] or '(none)'}",
        f"  allowed_patterns: {p['allowed_patterns'] or '(none)'}",
        f"  blocked_patterns: {p['blocked_patterns'] or '(none)'}",
    ]
    return "\n".join(lines)


def mode_warning(mode: str) -> str | None:
    if mode == "confirm":
        return (
            "mode is still 'confirm' — allowed_commands / allowed_patterns are not enforced "
            "until you `felix permissions set-mode allowlist` (or denylist)."
        )
    return None
