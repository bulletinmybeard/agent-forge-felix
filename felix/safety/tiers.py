"""Risk tiers for Felix actions.

The server's CommandGuard already classifies shell commands (it attaches a
``guard.threat`` to tool.call events and raises confirm.request for destructive
ops). Felix maps those signals — plus the tool name and the confirm prompt
text — onto four tiers that drive the gate decision.
"""

from __future__ import annotations

import ipaddress
import re
from enum import IntEnum
from typing import Any
from urllib.parse import urlsplit

# Outbound diagnostic tools. Read-only against local/internal/allowlisted hosts,
# but a probe to an arbitrary external host is an exfiltration channel (data in a
# URL query, a DNS subdomain, …), so those escalate to a confirm.
NETWORK_TOOLS = frozenset({"http_check", "dns_lookup", "net_probe"})

# Tools that only read state — never gated.
READ_ONLY_TOOLS = frozenset(
    {
        "docker_ps",
        "docker_logs",
        "docker_inspect",
        "docker_df",
        "docker_stats",
        "docker_images",
        "docker_volumes",
        "docker_networks",
        "docker_compose_status",
        "docker_cleanup_preview",
        "system_overview",
        "cpu_info",
        "memory_info",
        "disk_usage",
        "disk_io",
        "process_list",
        "gpu_info",
        "dns_lookup",
        "net_probe",
        "http_check",
        "analyze_logs",
        "read_file",
        "list_directory",
        "find_files",
        "grep_files",
        "grep_text",
        "health_check",
    }
)

# Mutating but low-blast-radius operations Felix may auto-apply with --apply.
_LOW_RISK_PATTERNS = (
    r"\bdocker\s+restart\b",
    r"\bdocker\s+image\s+prune\b(?!.*-a)",
    r"\bdocker\s+(container|network)\s+prune\b",
    r"\bsystemctl\s+(restart|reload)\b",
    r"\brm\s+-[a-z]*\s+/tmp/",
    r"\brm\s+-[a-z]*\s+.*\.(log|tmp|cache)\b",
)

# Never auto-run, even with --yes: needs an explicit per-step confirm.
_HIGH_RISK_PATTERNS = (
    r"\brm\s+-[a-z]*r[a-z]*f?\s+(/|~|\$HOME|/etc|/var|/usr)\b",
    r"\bdocker\s+volume\s+rm\b",
    r"\bdocker\s+system\s+prune\b.*--volumes",
    r"\bdrop\s+(database|table)\b",
    r"\b(iptables|ufw|firewall-cmd)\b",
    r"\b(passwd|usermod|chpasswd)\b",
    r"\b(apt|apt-get|yum|dnf|brew)\s+(install|upgrade|remove|purge)\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
)


class RiskTier(IntEnum):
    READ_ONLY = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(p, text) for p in patterns)


def _network_destination(args: dict[str, Any] | None) -> str | None:
    """Best-effort destination host from a network tool's args. None when no host
    is present (then the tool keeps its default read-only treatment)."""
    if not isinstance(args, dict):
        return None
    for key in ("url", "endpoint", "target", "host", "name", "domain", "address"):
        val = args.get(key)
        if not val or not isinstance(val, str):
            continue
        if "://" in val:
            return urlsplit(val).hostname
        return val.split("/")[0].split(":")[0].strip() or None
    return None


def _is_local_or_allowed(host: str, allow: frozenset[str]) -> bool:
    """A destination Felix may reach without a confirm: explicitly allowlisted,
    loopback/private IP, or an internal name (bare hostname / .local / .internal /
    cluster suffixes — e.g., a Docker service or k8s service)."""
    host = host.lower()
    if host in allow or host == "localhost":
        return True
    if host.endswith((".local", ".internal", ".svc", ".cluster.local")):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private or ip.is_link_local
    except ValueError:
        # Not an IP. A bare hostname (no dot) is an internal/container name.
        return "." not in host


def classify(
    *,
    tool_name: str | None = None,
    command: str | None = None,
    guard_threat: str | None = None,
    confirm_prompt: str | None = None,
    args: dict[str, Any] | None = None,
    egress_allow_hosts: list[str] | None = None,
) -> RiskTier:
    """Derive the highest applicable tier from the available signals.

    Order matters: explicit destructive signals win over the read-only
    allowlist so a read-named tool can never downgrade a dangerous command.
    Outbound diagnostics (NETWORK_TOOLS) to a non-local, non-allowlisted host
    escalate to MEDIUM so exfiltration via a probe can't ride the auto-approve
    of the read-only tier.
    """
    haystack = " ".join(filter(None, (command, confirm_prompt))).lower()

    threat = (guard_threat or "").lower()
    if threat in {"destructive", "high"} or (haystack and _matches_any(haystack, _HIGH_RISK_PATTERNS)):
        return RiskTier.HIGH

    if threat in {"sudo", "sudo_only", "medium"} or confirm_prompt:
        # A confirm prompt from the server means the op is at least medium.
        if haystack and _matches_any(haystack, _LOW_RISK_PATTERNS):
            return RiskTier.LOW
        return RiskTier.MEDIUM

    if haystack and _matches_any(haystack, _LOW_RISK_PATTERNS):
        return RiskTier.LOW

    if tool_name in NETWORK_TOOLS:
        dest = _network_destination(args)
        if dest is not None and not _is_local_or_allowed(
            dest, frozenset(h.lower() for h in (egress_allow_hosts or []))
        ):
            return RiskTier.MEDIUM
        # local / internal / allowlisted / no destination -> read-only below

    if tool_name and tool_name in READ_ONLY_TOOLS:
        return RiskTier.READ_ONLY

    # Unknown mutating tool with no guard signal: treat as medium and confirm.
    if tool_name:
        return RiskTier.MEDIUM

    return RiskTier.READ_ONLY
