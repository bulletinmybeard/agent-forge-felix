"""Verification: compare before/after probe snapshots into a verdict.

Tool output streams back as text, so signal extraction is heuristic — we scan
observation blobs for the markers that matter for the MVP domains (HTTP status,
container health, disk free %, error counts) and compare. The orchestrator owns
running the probes; this module is pure analysis so it stays unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Verdict(str, Enum):
    FIXED = "Fixed"
    PARTIAL = "Partial"
    FAILED = "Failed"
    PROPOSED = "Proposed"  # --read-only: a fix was proposed, not applied
    NOT_APPLIED = "Not Applied"  # --dry-run: nothing executed
    REJECTED = "Rejected"  # prompt out of scope — agent declined (no diagnosis)


_VERDICT_RE = re.compile(
    r"VERDICT\b[\s:*#`>_.-]*?\b(FIXED|PARTIAL|FAILED|PROPOSED|NOT[ _]APPLIED|REJECTED)\b",
    re.IGNORECASE,
)
_VERDICT_MAP = {
    "FIXED": Verdict.FIXED,
    "PARTIAL": Verdict.PARTIAL,
    "FAILED": Verdict.FAILED,
    "PROPOSED": Verdict.PROPOSED,
    "NOT APPLIED": Verdict.NOT_APPLIED,
    "NOT_APPLIED": Verdict.NOT_APPLIED,
    "REJECTED": Verdict.REJECTED,
}


def parse_verdict(report_text: str) -> Verdict | None:
    """Extract the agent's self-reported VERDICT line, if present.

    The @felix agent has the actual tool outputs and verifies before/after
    itself, so its verdict is more reliable than Felix's output-blind re-probe.
    """
    if not report_text:
        return None
    m = _VERDICT_RE.search(report_text)
    if not m:
        return None
    return _VERDICT_MAP.get(m.group(1).upper().replace("_", " "))


_HTTP_RE = re.compile(r"\bHTTP[/ ]?\d?\.?\d?\s*(\d{3})\b|\bstatus(?:[_ ]code)?[=: ]+(\d{3})\b", re.IGNORECASE)
_ACTIVE_FAIL_RE = re.compile(
    r"Restarting\s*\(\d+\)"  # docker ps STATUS: "Restarting (1) 5 seconds ago"
    r"|\(unhealthy\)"  # docker ps health suffix: "Up 2 minutes (unhealthy)"
    r'|"(?:Status|State)"\s*:\s*"(?:restarting|unhealthy)"',  # docker inspect .State
    re.IGNORECASE,
)
_DORMANT_RE = re.compile(
    r"Exited\s*\(\d+\)"  # "Exited (1) 2 hours ago"
    r'|"(?:Status|State)"\s*:\s*"(?:exited|created|dead)"',
    re.IGNORECASE,
)
_HEALTHY_RE = re.compile(
    r"\(healthy\)"  # docker ps health suffix
    r"|\bUp\s+\d"  # docker ps STATUS: "Up 5 minutes"
    r'|"(?:Status|State)"\s*:\s*"running"',  # docker inspect .State
    re.IGNORECASE,
)
_DISK_FREE_RE = re.compile(r"(\d{1,3})%\s*(?:used|full)", re.IGNORECASE)
_ERROR_RE = re.compile(r"\b(error|exception|traceback|connection refused|fatal)\b", re.IGNORECASE)


@dataclass
class Signals:
    http_statuses: list[int] = field(default_factory=list)
    unhealthy_hits: int = 0  # active failures only (unhealthy|restarting)
    dormant_hits: int = 0  # exited|created|dead — ambiguous, not verdict-driving
    healthy_hits: int = 0
    disk_used_pct: int | None = None
    error_hits: int = 0

    @property
    def ok_http(self) -> bool:
        return bool(self.http_statuses) and all(200 <= s < 400 for s in self.http_statuses)

    @property
    def bad_http(self) -> bool:
        return any(s >= 400 for s in self.http_statuses)


def extract_signals(observations: list[dict[str, Any]]) -> Signals:
    sig = Signals()
    for obs in observations:
        blob = _obs_text(obs)
        if not blob:
            continue
        for m in _HTTP_RE.finditer(blob):
            code = m.group(1) or m.group(2)
            if code:
                sig.http_statuses.append(int(code))
        sig.unhealthy_hits += len(_ACTIVE_FAIL_RE.findall(blob))
        sig.dormant_hits += len(_DORMANT_RE.findall(blob))
        sig.healthy_hits += len(_HEALTHY_RE.findall(blob))
        sig.error_hits += len(_ERROR_RE.findall(blob))
        disk = _DISK_FREE_RE.search(blob)
        if disk:
            sig.disk_used_pct = int(disk.group(1))
    return sig


def _obs_text(obs: dict[str, Any]) -> str:
    """Pull the human-readable payload out of an observation record."""
    parts: list[str] = []
    for key in ("output", "text", "result", "content", "summary", "finding"):
        val = obs.get(key)
        if isinstance(val, str):
            parts.append(val)
    if "raw" in obs and isinstance(obs["raw"], dict):
        parts.append(_obs_text(obs["raw"]))
    return "\n".join(parts)


@dataclass
class VerificationResult:
    verdict: Verdict
    rationale: list[str]
    before: Signals
    after: Signals


def contradicts_fixed(before: Signals, after: Signals) -> str | None:
    """Return a reason if a re-probe POSITIVELY contradicts a FIXED claim, else None."""
    if before.unhealthy_hits and after.unhealthy_hits >= before.unhealthy_hits:
        return f"active failure persists after fix: {before.unhealthy_hits} -> {after.unhealthy_hits}"
    if after.bad_http:
        return f"HTTP still failing after fix: {after.http_statuses}"
    return None


def decide(
    before: Signals,
    after: Signals,
    *,
    applied: bool,
    read_only: bool = False,
    dry_run: bool = False,
) -> VerificationResult:
    if dry_run:
        return VerificationResult(Verdict.NOT_APPLIED, ["dry-run: no changes executed"], before, after)
    if read_only or not applied:
        return VerificationResult(Verdict.PROPOSED, ["read-only: fix proposed, not applied"], before, after)

    improvements: list[str] = []
    regressions: list[str] = []

    # HTTP: a bad status that became OK is the strongest positive signal.
    if before.bad_http and after.ok_http:
        improvements.append(f"HTTP recovered: {before.http_statuses} -> {after.http_statuses}")
    elif before.bad_http and after.bad_http:
        regressions.append(f"HTTP still failing: {after.http_statuses}")

    if before.unhealthy_hits:
        if after.unhealthy_hits == 0 and after.healthy_hits:
            improvements.append(f"active failure cleared: {before.unhealthy_hits} -> 0, target now healthy/running")
        elif after.unhealthy_hits == 0:
            regressions.append("recovery unconfirmed: no post-activation health probe of the affected target")
        elif after.unhealthy_hits >= before.unhealthy_hits:
            regressions.append(f"active failure persists: {before.unhealthy_hits} -> {after.unhealthy_hits}")
        else:
            improvements.append(f"active failures reduced: {before.unhealthy_hits} -> {after.unhealthy_hits}")
            regressions.append(f"active failure still present: {after.unhealthy_hits} remaining")

    if before.disk_used_pct is not None and after.disk_used_pct is not None:
        if after.disk_used_pct < before.disk_used_pct:
            improvements.append(f"disk freed: {before.disk_used_pct}% -> {after.disk_used_pct}% used")
        elif after.disk_used_pct > before.disk_used_pct:
            regressions.append(f"disk usage grew: {before.disk_used_pct}% -> {after.disk_used_pct}% used")

    if after.error_hits > before.error_hits:
        regressions.append(f"new errors after fix: {before.error_hits} -> {after.error_hits}")

    if improvements and not regressions:
        verdict = Verdict.FIXED
    elif improvements and regressions:
        verdict = Verdict.PARTIAL
    elif regressions:
        verdict = Verdict.FAILED
    else:
        return VerificationResult(
            Verdict.FIXED,
            ["change applied; no runtime failure signal or regression detected (edit-level verification)"],
            before,
            after,
        )

    rationale = improvements + regressions or ["no measurable change between before and after probes"]
    return VerificationResult(verdict, rationale, before, after)
