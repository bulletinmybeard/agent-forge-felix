"""Rich renderer for the streaming run.

Maps each server event to a concise terminal line. Tokens stream inline; tool
calls, diffs, iterations, and errors get their own marked lines. ASCII status
markers only (no emoji), per house style: * ok, x fail, ! warn, i info, - list.
"""

from __future__ import annotations

import asyncio
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


def _fmt_duration(seconds: float) -> str:
    """Human duration: '37.4s' under a minute, else '4m 12s'."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}m {s}s"


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


class ConsoleUI:
    def __init__(self, *, quiet: bool = False) -> None:
        self.console = Console()
        self.quiet = quiet
        self._streaming = False
        self._active_status: object | None = None  # the live spinner, if any
        self._status_paused = False
        # Per-tool timing: the server doesn't forward agent.tool_exec on this
        # path, so we buffer each tool.call and print it (with a client-measured
        # duration) when the next event arrives.
        self._pending_tool: dict | None = None
        self._run_model = "?"

    # -- framing ---------------------------------------------------------
    def banner(self, prompt: str) -> None:
        self.console.print(Panel.fit(f"[bold]Felix[/bold]  {_TAGLINE}", border_style="cyan"))
        self.console.print(f"[dim]prompt:[/dim] {prompt}\n")

    def stage(self, label: str) -> None:
        self.console.print(f"[bold cyan]>[/bold cyan] {label}")

    def info(self, text: str) -> None:
        self.console.print(f"[dim]i[/dim] {text}")

    def warn(self, text: str) -> None:
        self.console.print(f"[yellow]![/yellow] {text}")

    def error(self, text: str) -> None:
        self.console.print(f"[red]x[/red] {text}")

    def success(self, text: str) -> None:
        self.console.print(f"[green]*[/green] {text}")

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

    def _flush_pending_tool(self) -> None:
        pt = self._pending_tool
        if pt is None:
            return
        self._pending_tool = None
        dur = time.perf_counter() - pt["start"]
        meta = f"[dim]({dur:.1f}s · {self._run_model})[/dim]"
        self.console.print(f"  [magenta]tool[/magenta] {pt['name']}{pt['detail']}{pt['marker']} {meta}")

    def _finish_stream(self) -> None:
        if self._streaming:
            self.console.print()
            self._streaming = False

    def _on_agent_routed(self, e: Event) -> None:
        conf = e.get("confidence", "")
        suffix = f" ({conf})" if conf else ""
        self.stage(f"routed -> {e.get('profile', '?')}{suffix}: {e.get('reason', '')}")

    def _on_agent_config(self, e: Event) -> None:
        self._run_model = e.get("model", "?")
        self.info(f"mode={e.get('mode', '?')} model={e.get('model', '?')} tools={e.get('tools', '?')}")

    def _on_agent_iteration(self, e: Event) -> None:
        self._finish_stream()
        self.console.print(
            f"[dim]iteration {e.get('iteration')}/{e.get('max_iterations')} ({e.get('messages_in_context')} msgs)[/dim]"
        )

    def _on_agent_thinking(self, e: Event) -> None:
        status = e.get("status")
        if status and not self.quiet:
            self.console.print(f"[dim]  thinking: {status}[/dim]")

    def _on_tool_call(self, e: Event) -> None:
        self._finish_stream()
        name = e.get("name", "?")
        threat = e.guard_threat
        marker = f" [red](guard: {threat})[/red]" if threat and threat != "safe" else ""
        args = e.get("args", {})
        arg_preview = args.get("command") if isinstance(args, dict) else None
        detail = f": {arg_preview}" if arg_preview else ""
        # Buffer — the line prints (with measured duration) when the next event
        # lands. _flush_pending_tool flushed any prior pending call already.
        self._pending_tool = {"name": name, "detail": detail, "marker": marker, "start": time.perf_counter()}

    def _on_agent_tool_exec(self, e: Event) -> None:
        if e.get("status") == "done" and e.get("is_error"):
            self.error(f"  {e.get('name')} failed")

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

    def _on_agent_cancelled(self, e: Event) -> None:
        self._finish_stream()
        self.warn("run cancelled")

    def _on_agent_summary(self, e: Event) -> None:
        # Superseded by run_footer(), which renders the full telemetry line
        # (time, counts, tokens, model chain) underneath the final report.
        pass

    # -- documents -------------------------------------------------------
    def markdown(self, md: str) -> None:
        self.console.print(Markdown(md))
