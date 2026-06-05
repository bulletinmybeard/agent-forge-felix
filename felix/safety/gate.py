"""The confirm-policy engine.

Given a risk tier and the run's mode flags, decide how to answer a server
confirm.request. The four mode flags compose to the most conservative outcome:

    --read-only  : deny anything above READ_ONLY (propose-only run)
    --dry-run    : handled upstream (no execution reaches the gate)
    --apply      : auto-approve READ_ONLY + LOW
    --yes        : also auto-approve MEDIUM (auto_accept "yes to all")

HIGH is never auto-approved. It is blocked unless --yes is set, and even then
it requires one explicit interactive confirm — Felix never runs a destructive
op fully unattended.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from felix.safety.tiers import RiskTier


class Decision(Enum):
    APPROVE = "approve"  # auto-approve, no prompt
    PROMPT = "prompt"  # ask the user interactively
    DENY = "deny"  # auto-deny without asking


@dataclass(frozen=True)
class ModeFlags:
    read_only: bool = False
    dry_run: bool = False
    apply: bool = False
    yes: bool = False
    deep: bool = False


@dataclass(frozen=True)
class GateResult:
    decision: Decision
    tier: RiskTier
    reason: str
    # When approving MEDIUM under --yes, set auto_accept so the server stops
    # prompting for the rest of the run.
    auto_accept: bool = False


def evaluate(tier: RiskTier, flags: ModeFlags) -> GateResult:
    if flags.read_only:
        if tier <= RiskTier.READ_ONLY:
            return GateResult(Decision.APPROVE, tier, "read-only probe")
        return GateResult(Decision.DENY, tier, "blocked by --read-only")

    if tier <= RiskTier.READ_ONLY:
        return GateResult(Decision.APPROVE, tier, "read-only")

    if tier == RiskTier.LOW:
        if flags.apply:
            return GateResult(Decision.APPROVE, tier, "low-risk auto-applied (--apply)")
        return GateResult(Decision.PROMPT, tier, "low-risk needs confirm (no --apply)")

    if tier == RiskTier.MEDIUM:
        if flags.yes:
            return GateResult(Decision.APPROVE, tier, "medium auto-confirmed (--yes)", auto_accept=True)
        return GateResult(Decision.PROMPT, tier, "medium-risk needs confirm")

    # HIGH
    if flags.yes:
        return GateResult(Decision.PROMPT, tier, "high-risk requires explicit confirm even with --yes")
    return GateResult(Decision.DENY, tier, "high-risk blocked by default (use --yes + confirm)")
