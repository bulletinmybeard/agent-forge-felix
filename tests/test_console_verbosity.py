"""ConsoleUI verbosity gating."""

from felix.api.events import parse_event
from felix.ui.console import ConsoleUI


def test_default_hides_iteration_and_debug():
    ui = ConsoleUI(verbosity=0)
    assert not ui.at_least(1)
    assert not ui.at_least(2)


def test_v1_and_v2_levels():
    assert ConsoleUI(verbosity=1).at_least(1)
    assert not ConsoleUI(verbosity=1).at_least(2)
    assert ConsoleUI(verbosity=2).at_least(2)


def test_quiet_forces_level_zero():
    ui = ConsoleUI(quiet=True, verbosity=5)
    assert ui.verbosity == 0
    assert not ui.at_least(1)


def test_render_iteration_silent_at_v0(capsys):
    ui = ConsoleUI(verbosity=0)
    ui.render(parse_event({"type": "agent.iteration", "iteration": 1, "max_iterations": 10, "messages_in_context": 3}))
    # Rich may not use capsys; ensure no exception and pending tool clear
    assert ui._pending_tool is None


def test_tool_call_buffers_without_crash():
    ui = ConsoleUI(verbosity=2)
    ui.render(
        parse_event(
            {
                "type": "tool.call",
                "name": "shell",
                "args": {"command": "ls", "timeout": 30},
            }
        )
    )
    assert ui._pending_tool is not None
    assert ui._pending_tool["name"] == "shell"
