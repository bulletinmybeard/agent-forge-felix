from felix.api.events import (
    confirm_response_message,
    parse_event,
    query_message,
)


def test_parse_tool_call_guard_threat():
    e = parse_event(
        {"type": "tool.call", "name": "shell", "args": {"command": "rm -rf /"}, "guard": {"threat": "destructive"}}
    )
    assert e.is_tool_call
    assert e.guard_threat == "destructive"


def test_parse_file_diff():
    e = parse_event({"type": "file.diff", "path": "/etc/x", "snapshot_id": "abc", "additions": 2, "deletions": 1})
    assert e.is_file_diff
    assert e.get("snapshot_id") == "abc"


def test_terminal_classification():
    assert parse_event({"type": "agent.result", "text": "done"}).is_terminal
    assert parse_event({"type": "agent.error", "message": "boom"}).is_terminal
    assert not parse_event({"type": "tool.call"}).is_terminal


def test_confirm_request_classification():
    e = parse_event({"type": "confirm.request", "request_id": "cr_1", "prompt": "ok?"})
    assert e.is_confirm_request


def test_query_message_shape():
    msg = query_message("@felix fix it", session_id="s1", source="felix")
    assert msg["type"] == "query"
    assert msg["text"] == "@felix fix it"
    assert msg["overrides"]["source"] == "felix"
    assert msg["overrides"]["incognito"] is True  # one-shot runs never bleed across sessions
    assert msg["session_id"] == "s1"


def test_confirm_response_shape():
    msg = confirm_response_message("cr_1", True, auto_accept=True)
    assert msg == {"type": "confirm.response", "request_id": "cr_1", "confirmed": True, "auto_accept": True}
