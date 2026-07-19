from felix.pipeline.planpreview import _format_result, trace_to_markdown


def test_tools_line_includes_source_and_truncates():
    tools = [f"t{i}" for i in range(30)]
    text = _format_result(
        "tools",
        {"tools": tools, "source": "custom agent 'felix' tools list"},
    )
    assert "30 tools" in text
    assert "t0" in text
    assert "+6 more" in text
    assert "custom agent 'felix'" in text


def test_trace_markdown_custom_agent_tools():
    md = trace_to_markdown(
        {
            "query": "@felix hi",
            "final_mode": "custom:felix",
            "total_elapsed_ms": 1,
            "steps": [
                {
                    "step": "tools",
                    "result": {
                        "tools": ["docker_ps", "docker_logs", "shell"],
                        "source": "custom agent 'felix' tools list",
                    },
                }
            ],
        }
    )
    assert "3 tools" in md
    assert "docker_ps" in md
    assert "custom agent 'felix'" in md


def test_scrub_mouse_sequences():
    from felix.ui.console import _scrub_mouse

    raw = "hello\x1b[<43;12;44M world"
    assert _scrub_mouse(raw) == "hello world"
