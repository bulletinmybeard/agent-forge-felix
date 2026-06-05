"""Best-effort secret redaction before persisting observations/commands.

The server redacts secrets before they reach the model,
but tool output and command strings stream back to Felix raw.
We scrub the obvious shapes (tokens, keys, passwords, auth headers, connection strings)
before writing to disk.
"""

from __future__ import annotations

import re

_PLACEHOLDER = "***REDACTED***"

# (pattern, group-to-keep-prefix). Each substitution keeps an identifying
# prefix so the redacted record stays readable.
_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(authorization:\s*bearer\s+)[A-Za-z0-9._\-]+"), r"\1" + _PLACEHOLDER),
    (re.compile(r"(?i)\b(x-api-key:\s*)[A-Za-z0-9._\-]+"), r"\1" + _PLACEHOLDER),
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd)(\s*[=:]\s*)\S+"), r"\1\2" + _PLACEHOLDER),
    (re.compile(r"\b(AKIA[0-9A-Z]{16})\b"), _PLACEHOLDER),  # AWS access key id
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})\b"), _PLACEHOLDER),  # GitHub tokens
    (re.compile(r"\b(sk-[A-Za-z0-9]{20,})\b"), _PLACEHOLDER),  # OpenAI-style keys
    (re.compile(r"(?i)\b([a-z]+://[^:@\s]+:)[^@\s]+(@)"), r"\1" + _PLACEHOLDER + r"\2"),  # creds in URLs
)


def redact(text: str) -> str:
    if not text:
        return text
    for pattern, repl in _RULES:
        text = pattern.sub(repl, text)
    return text


def redact_obj(obj: object) -> object:
    """Recursively redact strings inside dicts/lists for JSONL records."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    return obj
