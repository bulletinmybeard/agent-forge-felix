"""Rich renderer for the streaming run.

Maps each server event to a concise terminal line. Tokens stream inline; tool
calls, diffs, iterations, and errors get their own marked lines. ASCII status
markers only (no emoji), per house style: * ok, x fail, ! warn, i info, - list.

Verbosity (pytest-style ``-v`` / ``-vv``)::

  0  default — stages, tools, denies, report, footer
  1  + skill ids, richer routing/config, thinking status, confirm detail
  2  + iterations, tool args/outputs (truncated), unknown events, plan dump
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from contextlib import asynccontextmanager, contextmanager

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import SPINNERS
from rich.syntax import Syntax

from felix.api.events import Event
from felix.report import clean_agent_text

_TAGLINE = "diagnose. fix. verify."
_OUTPUT_CAP_V2 = 800  # chars of tool output at -vv


def _fmt_duration(seconds: float) -> str:
    """Human duration: '37.4s' under a minute, else '4m 12s'."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}m {s}s"


def _truncate(text: str, limit: int = _OUTPUT_CAP_V2) -> str:
    text = text.replace("\r\n", "\n").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


# Fix-It Felix hammer swing for the progress spinner.
# Registered into Rich's spinner table so console.status(spinner="felix_hammer")
# picks it up like any built-in.
SPINNERS["felix_hammer"] = {"interval": 250, "frames": ["🔨", "🔨", "🔨", "⚒️"]}


class _Working:
    """Mutable spinner-label handle yielded by ConsoleUI.working()."""

    def __init__(self, label: str) -> None:
        self.label = label

    def set(self, label: str) -> None:
        self.label = label


_MOUSE_CSI_RE = re.compile(
    r"\x1b\[\??(?:1000|1002|1003|1005|1006|1015|1016)[hl]"  # enable/disable
    r"|\x1b\[<\d+;\d+;\d+[Mm]"  # SGR mouse reports
    r"|\x1b\[M..."  # X10 mouse (3 bytes after M)
)


def _scrub_mouse(text: str) -> str:
    if not text or "\x1b" not in text:
        return text
    return _MOUSE_CSI_RE.sub("", text)


class ConsoleUI:
    def __init__(self, *, quiet: bool = False, verbosity: int = 0) -> None:
        self.console = Console()
        self.quiet = quiet
        self.verbosity = 0 if quiet else max(0, int(verbosity))
        self._streaming = False
        self._active_status: object | None = None  # the live spinner, if any
        self._status_paused = False
        # Per-tool timing: the server doesn't forward agent.tool_exec on this
        # path, so we buffer each tool.call and print it (with a client-measured
        # duration) when the next event arrives.
        self._pending_tool: dict | None = None
        self._run_model = "?"
        self._disable_mouse_tracking()

    @staticmethod
    def _disable_mouse_tracking() -> None:
        """Ask the terminal to stop emitting mouse reports into the TTY stream."""
        import sys

        try:
            if not sys.stdout.isatty():
                return
            sys.stdout.write("\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?1015l")
            sys.stdout.flush()
        except Exception:  # noqa: BLE001 — best-effort cosmetic fix
            pass

    def at_least(self, level: int) -> bool:
        return not self.quiet and self.verbosity >= level

    # -- framing ---------------------------------------------------------
    def _print(self, *args: object, **kwargs: object) -> None:
        """Console.print with mouse CSI stripped from string args."""
        cleaned = []
        for a in args:
            cleaned.append(_scrub_mouse(a) if isinstance(a, str) else a)
        self.console.print(*cleaned, **kwargs)  # type: ignore[arg-type]

    def banner(self, prompt: str) -> None:
        self._print(Panel.fit(f"[bold]Felix[/bold]  {_TAGLINE}", border_style="cyan"))
        self._print(f"[dim]prompt:[/dim] {_scrub_mouse(prompt)}\n")
        if self.at_least(1):
            self.info(f"verbosity={self.verbosity}")

    def stage(self, label: str) -> None:
        self._print(f"[bold cyan]>[/bold cyan] {_scrub_mouse(label)}")

    def info(self, text: str) -> None:
        if self.quiet:
            return
        self._print(f"[dim]i[/dim] {_scrub_mouse(text)}")

    def verbose(self, level: int, text: str) -> None:
        """Info-style line only when verbosity >= *level*."""
        if self.at_least(level):
            self._print(f"[dim]i[/dim] {_scrub_mouse(text)}")

    def debug(self, text: str) -> None:
        """-vv diagnostic line."""
        if self.at_least(2):
            self._print(f"[dim].[/dim] {_scrub_mouse(text)}")

    def warn(self, text: str) -> None:
        self._print(f"[yellow]![/yellow] {_scrub_mouse(text)}")

    def error(self, text: str) -> None:
        self._print(f"[red]x[/red] {_scrub_mouse(text)}")

    def success(self, text: str) -> None:
        if self.quiet:
            return
        self._print(f"[green]*[/green] {_scrub_mouse(text)}")

    @asynccontextmanager
    async def working(self, label: str):
        """Animated spinner with elapsed time during a long await.

        Updates ~once a second so we see work is happening even when the
        server emits no events for a while.
        """
        handle = _Working(label)
        start = time.monotonic()
        status = self.console.status(f"{label}...", spinner="felix_hammer")
        self._active_status = status
        self._status_paused = False
        status.start()

        async def _tick() -> None:
            while True:
                await asyncio.sleep(0.8)
                if self._status_paused:
                    continue
                secs = int(time.monotonic() - start)
                status.update(f"{handle.label}... ({secs}s)")

        ticker = asyncio.create_task(_tick())
        try:
            yield handle
        finally:
            ticker.cancel()
            try:
                await ticker
            except asyncio.CancelledError:
                pass
            status.stop()
            self._active_status = None

    @contextmanager
    def suspend(self):
        """Pause the active spinner so interactive input can read stdin cleanly.

        The spinner is a Rich Live display with its own refresh thread; it must
        be stopped or it keeps owning the terminal and the confirm prompt can't be answered.
        """
        status = self._active_status
        if status is None:
            yield
            return
        self._status_paused = True
        status.stop()
        try:
            yield
        finally:
            status.start()
            self._status_paused = False

    @contextmanager
    def spinner(self, label: str):
        """Synchronous spinner with elapsed time, for blocking CLI operations."""
        status = self.console.status(f"{label}...", spinner="felix_hammer")
        stop = threading.Event()
        start = time.monotonic()

        def _tick() -> None:
            while not stop.wait(0.8):
                status.update(f"{label}... ({int(time.monotonic() - start)}s)")

        status.start()
        ticker = threading.Thread(target=_tick, daemon=True)
        ticker.start()
        try:
            yield
        finally:
            stop.set()
            status.stop()

    def show_report(self, agent_text: str) -> None:
        """Render the final report narrative to the terminal as Markdown.

        The @felix report body is Markdown (tables, **bold**, code fences), so
        render it through Rich's Markdown for readable tables/formatting rather
        than dumping raw text. Falls back to plain print if Markdown chokes.
        """
        text = clean_agent_text(agent_text or "")
        if not text:
            return
        self.console.rule("[bold]Report[/bold]")
        try:
            self.console.print(Markdown(text))
        except Exception:  # noqa: BLE001 — never let rendering crash a run
            self.console.print(text)
        self.console.rule()

    def run_footer(
        self,
        *,
        overall_elapsed: float,
        models: list[str],
        total_tokens: int,
        iterations: int,
        tool_calls: int,
    ) -> None:
        """One dim line under the report: overall time, counts, tokens, and the model chain
        The run telemetry shows but Felix wasn't surfacing.
        """
        parts = [_fmt_duration(overall_elapsed)]
        if tool_calls:
            parts.append(f"{tool_calls} tool calls")
        if iterations:
            parts.append(f"{iterations} iters")
        if total_tokens:
            parts.append(f"{total_tokens:,} tokens")
        if models:
            parts.append("models: " + " -> ".join(models))
        if self.at_least(1):
            parts.append(f"v={self.verbosity}")
        self.console.print(f"[dim]{'  ·  '.join(parts)}[/dim]")

    # -- event stream ----------------------------------------------------
    def render(self, event: Event) -> None:
        # Flush the buffered tool line (with its measured duration) as soon as
        # the next event arrives — that's when the tool has finished.
        if event.type not in ("tool.calls.flush",):
            self._flush_pending_tool()
        handler = getattr(self, f"_on_{event.type.replace('.', '_')}", None)
        if handler is not None:
            handler(event)
        elif self.at_least(2) and event.type:
            self.debug(f"event {event.type}")

    def _flush_pending_tool(self) -> None:
        pt = self._pending_tool
        if pt is None:
            return
        self._pending_tool = None
        dur = time.perf_counter() - pt["start"]
        meta = f"[dim]({dur:.1f}s · {self._run_model})[/dim]"
        self._print(f"  [magenta]tool[/magenta] {pt['name']}{pt['detail']}{pt['marker']} {meta}")
        if self.at_least(2) and pt.get("args_extra"):
            self.debug(f"  args {pt['args_extra']}")

    def _finish_stream(self) -> None:
        if self._streaming:
            self.console.print()
            self._streaming = False

    def _on_agent_routed(self, e: Event) -> None:
        conf = e.get("confidence", "")
        suffix = f" ({conf})" if conf else ""
        self.stage(f"routed -> {e.get('profile', '?')}{suffix}: {e.get('reason', '')}")
        if self.at_least(1):
            agent = e.get("agent") or e.get("mode") or e.get("name")
            if agent:
                self.verbose(1, f"agent={agent}")

    def _on_agent_config(self, e: Event) -> None:
        self._run_model = e.get("model", "?")
        self.info(f"mode={e.get('mode', '?')} model={e.get('model', '?')} tools={e.get('tools', '?')}")
        if self.at_least(1):
            bits = []
            for key in ("profile", "max_iterations", "provider", "temperature"):
                if e.get(key) is not None:
                    bits.append(f"{key}={e.get(key)}")
            if bits:
                self.verbose(1, "  " + " ".join(bits))

    def _on_agent_iteration(self, e: Event) -> None:
        if not self.at_least(2):
            return
        self._finish_stream()
        self.console.print(
            f"[dim]iteration {e.get('iteration')}/{e.get('max_iterations')} ({e.get('messages_in_context')} msgs)[/dim]"
        )

    def _on_agent_thinking(self, e: Event) -> None:
        status = e.get("status")
        if status and self.at_least(1):
            self.console.print(f"[dim]  thinking: {status}[/dim]")

    def _on_tool_call(self, e: Event) -> None:
        self._finish_stream()
        name = e.get("name", "?")
        threat = e.guard_threat
        marker = f" [red](guard: {threat})[/red]" if threat and threat != "safe" else ""
        args = e.get("args", {})
        arg_preview = args.get("command") if isinstance(args, dict) else None
        detail = f": {arg_preview}" if arg_preview else ""
        args_extra = ""
        if self.at_least(2) and isinstance(args, dict):
            rest = {k: v for k, v in args.items() if k != "command"}
            if rest:
                try:
                    args_extra = _truncate(json.dumps(rest, default=str), 400)
                except Exception:  # noqa: BLE001
                    args_extra = _truncate(str(rest), 400)
            elif not arg_preview and args:
                try:
                    detail = f": {_truncate(json.dumps(args, default=str), 200)}"
                except Exception:  # noqa: BLE001
                    detail = f": {_truncate(str(args), 200)}"
        # Buffer — the line prints (with measured duration) when the next event
        # lands. _flush_pending_tool flushed any prior pending call already.
        self._pending_tool = {
            "name": name,
            "detail": detail,
            "marker": marker,
            "start": time.perf_counter(),
            "args_extra": args_extra,
        }

    def _on_agent_tool_exec(self, e: Event) -> None:
        if e.get("status") == "done" and e.get("is_error"):
            self.error(f"  {e.get('name')} failed")
        if not self.at_least(2) or e.get("status") != "done":
            return
        out = e.get("output") or e.get("result") or ""
        if not isinstance(out, str) or not out.strip():
            chars = e.get("output_chars")
            if chars and self.at_least(2):
                self.debug(f"  {e.get('name', 'tool')} done ({chars} chars; upgrade AgentForge for preview)")
            return
        name = e.get("name", "tool")
        trunc = " (truncated)" if e.get("output_truncated") else ""
        self.debug(f"  {name} output{trunc}:\n{_truncate(out)}")

    def _on_file_diff(self, e: Event) -> None:
        self._finish_stream()
        action = e.get("action", "edited")
        path = e.get("path", "?")
        self.console.print(f"  [yellow]{action}[/yellow] {path} (+{e.get('additions', 0)} -{e.get('deletions', 0)})")
        diff = e.get("diff_text")
        if diff and not self.quiet:
            self.console.print(Syntax(diff, "diff", theme="ansi_dark", line_numbers=False))

    def _on_result_chunk(self, e: Event) -> None:
        token = e.get("token", "")
        if token:
            self.console.print(token, end="", soft_wrap=True)
            self._streaming = True

    def _on_result_done(self, e: Event) -> None:
        self._finish_stream()

    def _on_agent_result(self, e: Event) -> None:
        self._finish_stream()

    def _on_agent_error(self, e: Event) -> None:
        self._finish_stream()
        self.error(e.get("message", "agent error"))
        if self.at_least(2) and e.get("detail"):
            self.debug(str(e.get("detail")))

    def _on_agent_cancelled(self, e: Event) -> None:
        self._finish_stream()
        self.warn("run cancelled")

    def _on_agent_summary(self, e: Event) -> None:
        # Superseded by run_footer(), which renders the full telemetry line
        # (time, counts, tokens, model chain) underneath the final report.
        if self.at_least(2):
            self.debug(
                f"summary iters={e.get('iterations')} tools={e.get('tool_calls')} tokens={e.get('total_tokens')}"
            )

    def _on_confirm_request(self, e: Event) -> None:
        if self.at_least(1) and not e.get("auto_accepted"):
            self.verbose(1, f"confirm.request id={e.get('request_id', '?')}")

    # -- documents -------------------------------------------------------
    def markdown(self, md: str) -> None:
        self.console.print(Markdown(md))
