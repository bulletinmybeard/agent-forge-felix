"""Prompt completeness check.

Felix targets *any* system-level problem, so preflight is permissive: it
proceeds whenever a target is inferable and only asks for more when the prompt
is impossible to act on — a dangling reference with no path/attachment, or a
prompt made entirely of vague filler ("make it work"). Pure heuristics; no model
call needed for the obvious cases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# "check this log", "fix the issue in the screenshot"
# and a demonstrative with no concrete target and no attachment.
_DANGLING_REF = re.compile(
    r"\b(this|that|the)\s+(log|logfile|screenshot|image|file|error|stack ?trace|output)\b",
    re.IGNORECASE,
)
_HAS_PATH = re.compile(r"(/[\w.\-]+)+|\b[\w.\-]+\.(log|ya?ml|json|env|conf|txt|png|jpe?g)\b", re.IGNORECASE)
_TOKEN = re.compile(r"[a-z0-9][a-z0-9._/-]*", re.IGNORECASE)

# Container/host identifiers a specific target even without a slash-path.
# Hyphen/dot/underscore compounds; plain prose ("image", "screenshot") never matches.
_IDENTIFIER = re.compile(r"\b[a-z0-9]+(?:[._-][a-z0-9]+)+\b", re.IGNORECASE)

# Words that carry no actionable target on their own. A prompt made of nothing
# but these (e.g., "make it work", "fix it") has no concrete thing to act on.
_VAGUE = {
    "make",
    "fix",
    "fixed",
    "work",
    "working",
    "works",
    "it",
    "this",
    "that",
    "the",
    "a",
    "an",
    "help",
    "please",
    "repair",
    "broken",
    "break",
    "thing",
    "things",
    "something",
    "stuff",
    "my",
    "is",
    "are",
    "be",
    "being",
    "get",
    "got",
    "to",
    "do",
    "does",
    "now",
    "again",
    "up",
    "down",
    "why",
    "how",
    "what",
    "not",
    "no",
    "cant",
    "can",
    "wont",
    "keeps",
    "of",
    "out",
    "in",
    "on",
    "and",
    "or",
    "please",
}

# Intent gate: how-to / tutorial / general-knowledge / authoring prompts with no
# problem to diagnose are out of scope — Felix diagnoses and fixes, it is not a
# general assistant. A diagnostic signal below overrides (e.g., "explain why
# docker-container-1 keeps crashing" is a diagnosis, not a how-to).
_INFORMATIONAL = re.compile(
    r"\b("
    r"how\s+(to|do|can|would|should|could)\b"
    r"|what(?:'s| is| are)\b|what\s+does\b"
    r"|best\s+way\b|better\s+way\b|recommended\s+way\b"
    r"|steps?\s+to\b|ways?\s+to\b|guide\s+(to|on|for)\b|tutorial\b|walk\s*through\b"
    r"|explain\b|teach\s+me\b|show\s+me\s+how\b|tell\s+me\s+about\b"
    r"|difference\s+between\b|pros?\s+and\s+cons?\b|when\s+should\s+i\b"
    r"|write\s+(me\s+)?(a|an|the|some)\b|create\s+a\s+(script|function|program)\b"
    r"|generate\s+(a|an|some)\b|give\s+me\s+(a|an|some|example)\b"
    r")",
    re.IGNORECASE,
)
_DIAGNOSTIC = re.compile(
    r"\b("
    r"diagnose|debug|troubleshoot|investigate|root\s*cause|repair|"
    r"fix|resolve|audit|harden|"
    r"why\s+(is|are|does|do|won'?t|can'?t|isn'?t|aren'?t)\b|"
    r"fail(s|ed|ing)?|broke(n)?|crash(es|ed|ing)?|"
    r"error|exception|traceback|unhealthy|restart(ing|s|ed)?|"
    r"timeout|timing\s+out|unreachable|refused|"
    r"not\s+(working|responding|starting|running|reachable)|"
    r"won'?t\s+(start|run|connect|boot)|can'?t\s+(connect|reach|start)|"
    r"out\s+of\s+(space|memory|disk|inodes)|full|leak|pressure|"
    r"hang(s|ing|ed)?|hung|stuck|slow|degraded|"
    r"\b[45]\d{2}\b"  # 4xx/5xx http status
    r")",
    re.IGNORECASE,
)


@dataclass
class Preflight:
    ok: bool
    reason: str = ""
    kind: str = "incomplete"  # "incomplete" (needs more info) | "out_of_scope"


def check(prompt: str, *, has_attachment: bool = False) -> Preflight:
    text = prompt.strip()
    if len(text) < 3:
        return Preflight(False, "prompt is empty or too short to act on")

    # Intent gate: a how-to / informational / authoring prompt with no problem to
    # diagnose is out of scope. A diagnostic signal (fix / why-failing / symptom)
    # overrides — so "fix the 502 on api" and "why does X crash" still pass.
    if _INFORMATIONAL.search(text) and not _DIAGNOSTIC.search(text):
        return Preflight(
            False,
            "this is a how-to / informational question — Felix diagnoses and fixes system problems, not a general assistant",
            kind="out_of_scope",
        )

    # A dangling demonstrative blocks only when it's the SOLE target: no path, no
    # attachment, and no concrete identifier elsewhere. A named container/host
    # makes "the image" resolvable from context.
    if (
        _DANGLING_REF.search(text)
        and not _HAS_PATH.search(text)
        and not has_attachment
        and not _IDENTIFIER.search(text)
    ):
        ref = _DANGLING_REF.search(text)
        target = ref.group(0) if ref else "target"
        return Preflight(False, f"'{target}' referenced but no path or attachment provided")

    # A path or attachment is always enough to act on.
    if has_attachment or _HAS_PATH.search(text):
        return Preflight(True)

    # Otherwise proceed as long as the prompt has at least one concrete token —
    # any noun/identifier beyond vague filler. Felix infers the domain itself.
    tokens = [t.lower() for t in _TOKEN.findall(text)]
    content = [t for t in tokens if t not in _VAGUE and len(t) > 1]
    if not content:
        return Preflight(False, "no concrete target — the prompt is only vague words")

    return Preflight(True)
