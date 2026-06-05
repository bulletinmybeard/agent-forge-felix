"""Interactive confirmation prompt for medium/high-risk actions.

Returns (confirmed, auto_accept). "all" is only offered for medium-risk;
a high-risk action always demands a discrete yes and never enables auto-accept.
"""

from __future__ import annotations

from rich.console import Console
from rich.prompt import Prompt

from felix.safety.tiers import RiskTier


def ask_confirm(prompt: str, tier: RiskTier, *, console: Console | None = None) -> tuple[bool, bool]:
    console = console or Console()
    label = {RiskTier.LOW: "low", RiskTier.MEDIUM: "medium", RiskTier.HIGH: "high"}.get(tier, "?")
    color = {"low": "yellow", "medium": "yellow", "high": "red"}[label]
    console.print(f"\n[{color}]confirm[/{color}] [{label} risk] {prompt}")

    if tier == RiskTier.HIGH:
        answer = Prompt.ask("  proceed?", choices=["y", "n"], default="n", console=console)
        return answer == "y", False

    answer = Prompt.ask("  proceed?", choices=["y", "n", "all"], default="n", console=console)
    if answer == "all":
        return True, True
    return answer == "y", False
