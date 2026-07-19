"""Unit tests for command-permission merge helpers and REST wiring."""

from unittest.mock import MagicMock

from felix.api.rest import RestClient
from felix.permissions import (
    empty_policy,
    merge_list_edit,
    mode_warning,
    normalize_policy,
    overrides_put_body,
    set_mode,
)


def test_normalize_none_is_empty():
    p = normalize_policy(None)
    assert p == empty_policy()
    assert p["mode"] == "confirm"


def test_set_mode_preserves_lists():
    base = normalize_policy(
        {
            "mode": "confirm",
            "allowed_commands": ["ls", "df"],
            "blocked_patterns": [r"rm\s+-rf"],
        }
    )
    out = set_mode(base, "allowlist")
    assert out["mode"] == "allowlist"
    assert out["allowed_commands"] == ["ls", "df"]
    assert out["blocked_patterns"] == [r"rm\s+-rf"]


def test_merge_list_edit_add_dedupes():
    p = normalize_policy({"allowed_commands": ["ls"]})
    out = merge_list_edit(p, add={"allowed_commands": ["ls", "df", "docker"]})
    assert out["allowed_commands"] == ["ls", "df", "docker"]


def test_merge_list_edit_remove():
    p = normalize_policy({"allowed_commands": ["ls", "df", "docker"]})
    out = merge_list_edit(p, remove={"allowed_commands": ["df"]})
    assert out["allowed_commands"] == ["ls", "docker"]


def test_overrides_put_body_shape():
    body = overrides_put_body("shell", {"mode": "allowlist", "allowed_commands": ["ls"]})
    assert body == {
        "shell": {
            "mode": "allowlist",
            "allowed_commands": ["ls"],
            "allowed_patterns": [],
            "blocked_patterns": [],
        }
    }


def test_mode_warning_on_confirm():
    assert mode_warning("confirm") is not None
    assert mode_warning("allowlist") is None


def test_rest_put_and_validate():
    client = MagicMock()
    rest = RestClient.__new__(RestClient)
    rest._client = client

    put_resp = MagicMock()
    put_resp.content = b'{"ok":true}'
    put_resp.json.return_value = {"ok": True}
    put_resp.raise_for_status = MagicMock()
    client.put.return_value = put_resp

    out = rest.put_command_overrides({"shell": empty_policy()})
    assert out == {"ok": True}
    client.put.assert_called_once()
    assert client.put.call_args[0][0] == "/api/permissions/commands/overrides"

    val_resp = MagicMock()
    val_resp.content = b"{}"
    val_resp.json.return_value = {"action": "deny", "reason": "blocked", "source": "override"}
    val_resp.raise_for_status = MagicMock()
    client.post.return_value = val_resp

    v = rest.validate_command("shell", "rm -rf /")
    assert v["action"] == "deny"
    client.post.assert_called_once()
    assert client.post.call_args[0][0] == "/api/permissions/commands/validate"


def test_rest_delete_with_tool():
    client = MagicMock()
    rest = RestClient.__new__(RestClient)
    rest._client = client
    del_resp = MagicMock()
    del_resp.content = b'{"deleted":1}'
    del_resp.json.return_value = {"deleted": 1}
    del_resp.raise_for_status = MagicMock()
    client.delete.return_value = del_resp

    out = rest.delete_command_overrides("shell")
    assert out["deleted"] == 1
    client.delete.assert_called_once_with(
        "/api/permissions/commands/overrides",
        params={"tool": "shell"},
    )
