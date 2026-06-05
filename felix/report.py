"""Assemble the structured final report (report.md).

Section order is fixed by the spec. The @felix agent's result text carries the
narrative (Summary, Root Cause, etc.); Felix appends the sections it owns from
the run record: Verification, Risk Notes, Rollback Instructions, Commands Run,
Files Changed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from felix.verify.verifier import VerificationResult

_END_MARKER = re.compile(r"(?im)^[-*\s]*end of (report|transmission)\b")
# The fixed report section labels (see felix.md). Anchoring on these — rather
# than any ALL-CAPS run — avoids merging a verdict value like "NOT APPLIED" with
# the following label.
_KNOWN_LABELS = [
    "SUMMARY",
    "CURRENT STATUS",
    "EXPECTED GOAL",
    "EVIDENCE",
    "ROOT CAUSE",
    "FIX APPLIED / PROPOSED FIX",
    "FIX PROPOSED",
    "FIX APPLIED",
    "VERIFICATION",
    "RISK NOTES",
    "ROLLBACK INSTRUCTIONS",
    "COMMANDS RUN",
    "FILES CHANGED",
    "VERDICT",
]


def _leading_label(line: str) -> str | None:
    for lab in _KNOWN_LABELS:
        if line.startswith(lab + ":"):
            return lab
    return None


def clean_agent_text(text: str) -> str:
    """Defend against model output quirks: `|||` separators, runaway repetition
    (the whole report emitted several times), and conversational sign-offs.

    Normalize separators, cut at any END OF REPORT marker, put each known
    section label on its own line, then keep only the first copy of the report
    (stop where it restarts) with one of each section.
    """
    if not text:
        return ""
    text = text.replace("|||", "\n")
    end = _END_MARKER.search(text)
    if end:
        text = text[: end.start()]
    # Put each known label on its own line (longest first so compound labels
    # like "FIX APPLIED / PROPOSED FIX" aren't split by "FIX APPLIED").
    for lab in sorted(_KNOWN_LABELS, key=len, reverse=True):
        text = re.sub(r"[ \t]*" + re.escape(lab) + r"\s*:", "\n" + lab + ":", text)

    first_lab: str | None = None
    seen: set[str] = set()
    out: list[str] = []
    for line in text.split("\n"):
        lab = _leading_label(line.strip())
        if lab:
            if first_lab is None:
                first_lab = lab
            elif lab == first_lab:
                break  # the report is repeating — stop at the second copy
            if lab in seen:
                continue  # a duplicate section within the copy (e.g., early VERDICT)
            seen.add(lab)
        out.append(line)
    return _format_markdown(_collapse_dupes("\n".join(out)).split("\n"))


def _format_markdown(lines: list[str]) -> str:
    """Render the deduped report as clean Markdown: each section label bolded on
    its own line with blank-line separation, so a Markdown renderer shows
    distinct blocks (paragraphs, tables, code fences) instead of merging
    sections into one run-on paragraph."""
    md: list[str] = []
    for line in lines:
        lab = _leading_label(line.strip())
        if lab:
            rest = line.strip()[len(lab) + 1 :].strip()  # content after "LABEL:"
            md.extend(["", f"**{lab}:**", ""])
            if rest:
                md.append(rest)
        else:
            md.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(md)).strip()


def _collapse_dupes(text: str) -> str:
    """Drop consecutive duplicate/blank-run lines; final tidy."""
    out: list[str] = []
    prev: str | None = None
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped and stripped == prev:
            continue
        out.append(line)
        prev = stripped
    return "\n".join(out).strip()


def _command_label(c: dict[str, Any]) -> str:
    """Render a command/tool call: the shell command, or name(arg=val, ...)."""
    if c.get("command"):
        return str(c["command"])
    name = c.get("name", "(unknown)")
    args = c.get("args")
    if isinstance(args, dict) and args:
        preview = ", ".join(f"{k}={v}" for k, v in list(args.items())[:3])
        return f"{name}({preview})"
    return str(name)


@dataclass
class ReportInput:
    prompt: str
    agent_text: str  # the @felix structured narrative
    verification: VerificationResult | None
    commands: list[dict[str, Any]]
    file_changes: list[dict[str, Any]]
    risk_notes: list[str]
    rollback_hints: list[str]


def build_report(data: ReportInput) -> str:
    lines: list[str] = []

    lines.append("# Felix Report")
    lines.append("")
    lines.append(f"Prompt: {data.prompt}")
    lines.append("")

    # The agent narrative already covers Summary / Current Status / Expected
    # Goal / Evidence / Root Cause / Fix. Clean model output quirks first.
    lines.append(clean_agent_text(data.agent_text))
    lines.append("")

    lines.append("## Verification")
    if data.verification:
        v = data.verification
        lines.append(f"Result: {v.verdict.value}")
        for r in v.rationale:
            lines.append(f"- {r}")
    else:
        lines.append("- not run")
    lines.append("")

    lines.append("## Risk Notes")
    if data.risk_notes:
        lines.extend(f"- {n}" for n in data.risk_notes)
    else:
        lines.append("- no elevated-risk actions taken")
    lines.append("")

    lines.append("## Rollback Instructions")
    if data.rollback_hints:
        lines.extend(f"- {h}" for h in data.rollback_hints)
    else:
        lines.append("- no changes to roll back")
    lines.append("")

    lines.append("## Commands Run")
    if data.commands:
        for c in data.commands:
            tier = c.get("tier", "?")
            applied = "applied" if c.get("applied") else "proposed"
            lines.append(f"- [{tier}/{applied}] {_command_label(c)}")
    else:
        lines.append("- none")
    lines.append("")

    lines.append("## Files Changed")
    if data.file_changes:
        for f in data.file_changes:
            path = f.get("file_path", "(unknown)")
            adds = f.get("additions", "?")
            dels = f.get("deletions", "?")
            lines.append(f"- {path} (+{adds} -{dels})")
    else:
        lines.append("- none")
    lines.append("")

    return "\n".join(lines)
