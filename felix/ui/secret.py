"""Masked sudo-password prompt for felix (mirrors ui/confirm.py)."""

from __future__ import annotations

import getpass
import sys
from collections.abc import Callable


def ask_secret(
    prompt: str,
    getpass_fn: Callable[[str], str] = getpass.getpass,
    isatty: Callable[[], bool] = sys.stdin.isatty,
) -> str | None:
    if not isatty():
        return None
    value = getpass_fn(f"  {prompt}: ").strip()
    return value or None
