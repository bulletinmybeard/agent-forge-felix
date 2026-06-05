"""Render the /api/dry-run pipeline trace into a plan.md preview.

The dry-run trace shows how agent-forge will route the prompt — mode, profile,
model, tools, and skills — without executing anything. Felix uses it both for
the `--dry-run` flag and as the investigation-plan preview before a live run.
"""

from __future__ import annotations

from typing import Any

# Steps worth surfacing, in display order, with a friendly label.
_HIGHLIGHT = {
    "custom_agent": "Agent",
    "heuristic": "Heuristic class",
    "llm_classifier": "LLM classifier",
    "arbitration": "Mode",
    "profile_routing": "Profile",
    "model_resolution": "Model",
    "tools": "Tools",
    "skills": "Skills",
}


def trace_to_markdown(trace: dict[str, Any]) -> str:
    lines = ["# Investigation Plan (preview)", ""]
    lines.append(f"Query: {trace.get('query', '')}")
    lines.append(f"Resolved mode: {trace.get('final_mode', '?')}")
    lines.append(f"Trace time: {trace.get('total_elapsed_ms', 0)} ms")
    lines.append("")

    for step in trace.get("steps", []):
        step_id = step.get("step")
        if step_id not in _HIGHLIGHT:
            continue
        result = step.get("result", {})
        lines.append(f"## {_HIGHLIGHT[step_id]}")
        lines.append(_format_result(step_id, result))
        lines.append("")

    return "\n".join(lines)


def _format_result(step_id: str, result: dict[str, Any]) -> str:
    if step_id == "tools":
        tools = result.get("tools", [])
        return f"- {len(tools)} tools: {', '.join(tools)}" if tools else "- (none)"
    if step_id == "skills":
        skills = result.get("skills", [])
        if not skills:
            return "- (none)"
        return "\n".join(f"- {s.get('id')}: {s.get('description', '')}" for s in skills)
    if step_id == "profile_routing":
        return f"- {result.get('profile', '?')} — {result.get('reason', '')}"
    if step_id == "model_resolution":
        return f"- {result.get('model', '?')} (profile {result.get('profile_name', '?')})"
    if step_id == "arbitration":
        return f"- {result.get('mode', '?')} (winner: {result.get('winner', '?')})"
    if step_id in {"heuristic", "llm_classifier"}:
        return f"- {result.get('mode', '?')} (confidence {result.get('confidence', result.get('error', 'n/a'))})"
    if step_id == "custom_agent":
        if result.get("detected"):
            return f"- {result.get('agent_name')} (mode {result.get('mode')})"
        return "- none"
    return f"- {result}"
