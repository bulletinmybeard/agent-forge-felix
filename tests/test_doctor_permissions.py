"""Doctor check for AgentForge command permissions API."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from felix.doctor import _check_command_permissions


def test_permissions_ok_lists_modes():
    rest = MagicMock()
    rest.command_permissions.return_value = {
        "shell": {"effective": {"mode": "confirm"}, "override": None},
        "ssh": {"effective": {"mode": "allowlist"}, "override": {"mode": "allowlist"}},
    }
    check = _check_command_permissions(rest)
    assert check.ok is True
    assert "shell=confirm" in check.detail
    assert "ssh=allowlist+override" in check.detail


def test_permissions_missing_404():
    rest = MagicMock()
    req = httpx.Request("GET", "http://localhost/api/permissions/commands")
    resp = httpx.Response(404, request=req)
    rest.command_permissions.side_effect = httpx.HTTPStatusError("missing", request=req, response=resp)
    check = _check_command_permissions(rest)
    assert check.ok is False
    assert "0.12" in check.detail


def test_permissions_other_error():
    rest = MagicMock()
    rest.command_permissions.side_effect = RuntimeError("connection refused")
    check = _check_command_permissions(rest)
    assert check.ok is False
    assert "connection refused" in check.detail
