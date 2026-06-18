"""Run lifecycle orchestrator.

Drives a Felix run end to end: plan preview > before-probe > main repair run > after-probe > verify > report.
Owns the WebSocket loop, answers confirm.request via the safety gate, and tees every event into the run-store.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from felix.api.events import Event, cancel_message, confirm_response_message, query_message, secret_response_message
from felix.api.rest import RestClient
from felix.api.ws import WSClient
from felix.config import Config, felix_home
from felix.pipeline.planpreview import trace_to_markdown
from felix.report import ReportInput, build_report
from felix.runstore.ledger import ChangeRecord, Ledger
from felix.runstore.store import RunStore
from felix.runstore.undo import _revert_one
from felix.safety.gate import Decision, ModeFlags, evaluate
from felix.safety.tiers import RiskTier, classify
from felix.skills.retrieve import SkillRetriever
from felix.ui.confirm import ask_confirm
from felix.ui.console import ConsoleUI
from felix.ui.secret import ask_secret
from felix.verify.verifier import (
    Signals,
    Verdict,
    VerificationResult,
    contradicts_fixed,
    decide,
    extract_signals,
    parse_verdict,
)


@dataclass
class DriveResult:
    session_id: str = ""
    agent_text: str = ""
    observations: list[dict[str, Any]] = field(default_factory=list)
    commands: list[dict[str, Any]] = field(default_factory=list)
    file_changes: list[dict[str, Any]] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)
    rollback_hints: list[str] = field(default_factory=list)
    applied_any: bool = False
    # True when the most recent file edit was NOT followed by an activation
    # command (build/deploy/restart/...). Flags the "edited then stopped" miss.
    edited_since_activation: bool = False
    error: str | None = None
    # Run telemetry from agent.summary (sent right after agent.result).
    elapsed: float = 0.0
    iterations: int = 0
    tool_call_count: int = 0
    total_tokens: int = 0
    models: list[str] = field(default_factory=list)


# Commands that make a changed input take effect in the running target.
# Broad on purpose: a missed match only costs one harmless re-prompt, while a false match
# just skips the backstop (same as before this existed). Generic across stacks
# and not Docker-specific.
_ACTIVATION_RE = re.compile(
    r"\b("
    r"deploy|redeploy|rollout|"
    r"docker\s+build|buildx|--build|compose\s+(?:up|build)|\bup\s+-d\b|force-recreate|recreate|"
    r"restart|reload|"
    r"systemctl|service\b|launchctl|"
    r"kubectl\s+apply|helm\s+(?:up|install)|terraform\s+apply|nixos-rebuild|ansible|"
    r"make\s+\w*(?:deploy|build|install|up)|"
    r"scp|rsync|"
    r"migrate"
    r")\b",
    re.IGNORECASE,
)


def _is_activation_command(command: str) -> bool:
    """True when a shell/ssh command makes a changed input take effect

    (build, deploy, restart, copy-to-target, ...). Used to detect an edit that was never activated.
    """
    return bool(_ACTIVATION_RE.search(command or ""))


def _enforcement_nudge(target_was_broken: bool, applied_any: bool, edited_since_activation: bool) -> str | None:
    """What the fix-enforcement loop should do before verifying:

    * ``"apply"``    — the target was broken but nothing was applied (the agent
      diagnosed and stopped at PROPOSED/NOT APPLIED).
    * ``"activate"`` — a change was applied but not activated in the target.
    * ``None``       — nothing to enforce: the target was healthy to begin with,
      or a change was applied AND activated (let verification judge the result).
    """
    if not target_was_broken:
        return None
    if not applied_any:
        return "apply"
    if edited_since_activation:
        return "activate"
    return None


def _spinner_label(event: Event, default: str) -> str:
    """Short status text for the spinner, derived from the latest event."""
    t = event.type
    if t == "agent.routing":
        return "routing"
    if t in ("agent.routed", "agent.config"):
        return "starting agent"
    if t == "tool.calls.flush":
        return "running tools"
    if t == "agent.thinking":
        return str(event.get("status") or "thinking")
    if t == "agent.iteration":
        return f"iteration {event.get('iteration')}"
    if t == "result.chunk":
        return "writing report"
    if t == "agent.result":
        return "finishing"
    return default


class Orchestrator:
    def __init__(
        self,
        config: Config,
        flags: ModeFlags,
        ui: ConsoleUI,
        store: RunStore,
        *,
        discover: bool = False,
    ) -> None:
        self.config = config
        self.flags = flags
        self.ui = ui
        self.store = store
        self.discover = discover
        self.ledger = Ledger(store.path)
        self.retriever = SkillRetriever(config)
        # Set once the user answers "all" — makes "yes to all" sticky client-side
        # so parallel confirm prompts (e.g., a model proposing several edits at
        # once) don't re-prompt before the server's auto-accept propagates.
        self._auto_accept_all = False
        self._pending_file_changes: list[dict[str, Any]] = []

    async def run(self, prompt: str) -> Verdict:
        self.store.write_prompt(prompt)
        run_start = time.perf_counter()

        # Plan preview (also the --dry-run stopping point).
        self._plan_preview(prompt)
        if self.flags.dry_run:
            self.ui.info("dry-run: stopping before execution")
            verdict = Verdict.NOT_APPLIED
            self._finalize(prompt, main=DriveResult(), verification=None, verdict=verdict)
            return verdict

        # Before-state probe (read-only).
        self.ui.stage("assessing current state")
        before = await self._drive(self._probe_text(prompt), self._readonly_flags(), phase="before")
        for obs in before.observations:
            self.store.append_observation({**obs, "phase": "before"})

        # Retrieve relevant skills from the fleet and inject them.
        overrides = await self._retrieve_skills(prompt)

        # Main repair run (full flags).
        self.ui.stage("investigating and repairing")
        main = await self._drive(self._main_text(prompt), self.flags, phase="run", overrides=overrides)
        for obs in main.observations:
            self.store.append_observation({**obs, "phase": "run"})

        # Fix-enforcement loop. On a writable run, if the target was actually broken to begin with,
        # don't accept "diagnosed but didn't apply" or "applied but didn't activate" as terminal
        # and re-drive the agent toward a working fix. This is the structural guarantee
        # (prompt mandates only raise compliance): some models (e.g., minimax) stop at PROPOSED. Bounded,
        # gated on the before-state being broken (so a healthy host stays a clean NOT APPLIED),
        # and sequential (safe under concurrent-request caps).
        enforced = await self._enforce_fix(prompt, before, main, overrides)

        # After-state probe + verification. After any enforcement re-drive,
        # force a live re-probe so a stale self-verdict from an earlier turn can't win.
        verification = await self._verify(prompt, before, main, force_reprobe=enforced)

        # Report + finalize.
        self._finalize(prompt, main=main, verification=verification, verdict=verification.verdict)
        self.ui.show_report(main.agent_text)
        self.ui.run_footer(
            overall_elapsed=time.perf_counter() - run_start,
            models=main.models,
            total_tokens=main.total_tokens,
            iterations=main.iterations,
            tool_calls=main.tool_call_count,
        )
        return verification.verdict

    # -- stages ----------------------------------------------------------
    def _plan_preview(self, prompt: str) -> None:
        try:
            with RestClient(self.config) as rest:
                trace = rest.dry_run(self._main_text(prompt))
            md = trace_to_markdown(trace)
            self.store.write_plan(md)
            self.ui.info(f"plan: mode={trace.get('final_mode', '?')}")
        except Exception as exc:  # noqa: BLE001 — preview is best-effort
            self.ui.warn(f"plan preview unavailable: {exc}")

    async def _verify(
        self, prompt: str, before: DriveResult, main: DriveResult, *, force_reprobe: bool = False
    ) -> VerificationResult:
        # Out-of-scope rejection (felix.md scope gate, for prompts that slipped
        # past the client preflight heuristic) short-circuits everything — no
        # before/after, just surface the agent's REJECTED verdict.
        if parse_verdict(main.agent_text) == Verdict.REJECTED:
            self.ui.warn("verification: Rejected (out of scope)")
            return VerificationResult(Verdict.REJECTED, ["agent rejected: out of scope"], Signals(), Signals())

        if self.flags.read_only or not main.applied_any:
            result = decide(Signals(), Signals(), applied=main.applied_any, read_only=self.flags.read_only)
            self.ui.info(f"verification: {result.verdict.value}")
            return result

        # A fix was applied. A self-reported NON-success is trusted outright — the
        # agent had the tool outputs and there's no success claim to second-guess.
        # After an activation pass the main run's self-verdict is stale, so skip
        # this and force the live re-probe below.
        agent_verdict = parse_verdict(main.agent_text)
        if not force_reprobe and agent_verdict is not None and agent_verdict != Verdict.FIXED:
            self.ui.warn(f"verification: {agent_verdict.value} (agent-verified)")
            return VerificationResult(
                agent_verdict, ["agent self-verified (before/after in report)"], Signals(), Signals()
            )

        # Re-probe the target. Needed both when the agent gave no verdict (cut-off
        # turn) AND to cross-check a self-reported FIXED — the agent sometimes
        # verifies against stale state (e.g., a restart that didn't recreate the
        # container), so a blind "FIXED" can't be trusted on its own.
        self.ui.stage("verifying (re-checking state)")
        after = await self._drive(self._after_probe_text(prompt), self._readonly_flags(), phase="after")
        for obs in after.observations:
            self.store.append_observation({**obs, "phase": "after"})

        # Probe observations fold in the probe narratives — they carry the HTTP
        # status / health / disk signals.
        bsig = extract_signals(before.observations + [{"output": before.agent_text}])
        asig = extract_signals(after.observations + [{"output": after.agent_text}])

        if not force_reprobe and agent_verdict == Verdict.FIXED:
            # Honor the agent's FIXED unless the re-probe positively contradicts it.
            # Inconclusive re-probe defers to the agent (it had the real outputs).
            reason = contradicts_fixed(bsig, asig)
            if reason:
                self.ui.warn("verification: Failed (agent claimed Fixed; re-probe contradicts)")
                return VerificationResult(Verdict.FAILED, [f"agent claimed FIXED but {reason}"], bsig, asig)
            self.ui.success("verification: Fixed (agent-verified; re-probe consistent)")
            return VerificationResult(Verdict.FIXED, ["agent self-verified; re-probe consistent"], bsig, asig)

        # No agent verdict (cut-off turn), or forced re-probe after an activation
        # pass — decide from the live re-probe alone.
        result = decide(bsig, asig, applied=main.applied_any)
        marker = self.ui.success if result.verdict == Verdict.FIXED else self.ui.warn
        marker(f"verification: {result.verdict.value}")
        for r in result.rationale:
            self.ui.info(f"  {r}")
        return result

    def _finalize(
        self,
        prompt: str,
        *,
        main: DriveResult,
        verification: VerificationResult | None,
        verdict: Verdict,
    ) -> None:
        if main.file_changes:
            self.store.write_fix_plan(self._fix_plan_md(main))
        report = build_report(
            ReportInput(
                prompt=prompt,
                agent_text=main.agent_text or "(no narrative returned)",
                verification=verification,
                commands=main.commands,
                file_changes=main.file_changes,
                risk_notes=main.risk_notes,
                rollback_hints=main.rollback_hints,
            )
        )
        self.store.write_report(report)
        self.store.write_meta(
            {
                "run_id": self.store.run_id,
                "prompt": prompt,
                "agent": self.config.default_agent,
                "flags": vars(self.flags),
                "verdict": verdict.value,
                "applied": main.applied_any,
            }
        )
        if verification:
            self.ledger.stamp_verification(verdict.value)
        self.ui.info(f"run saved to {self.store.path}")

    # -- WebSocket drive -------------------------------------------------
    def _spawn_discovery(self, prompt: str) -> None:
        """Kick off skill discovery (search skills.sh > pull > index) in a
        DETACHED background process so it never blocks the run. New skills land
        in Qdrant for the NEXT run; this run proceeds with what's already there."""
        log_path = felix_home() / "discover.log"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            logf = open(log_path, "a")  # noqa: SIM115 — handed to the child process
            subprocess.Popen(
                [sys.executable, "-m", "felix.cli", "skills", "discover", prompt],
                stdout=logf,
                stderr=logf,
                start_new_session=True,  # survive Felix exiting
            )
            self.ui.info(f"discovering skills in background -> available next run (log: {log_path})")
        except Exception as exc:  # noqa: BLE001 — discovery is best-effort
            self.ui.warn(f"could not start background discovery: {exc}")

    async def _retrieve_skills(self, prompt: str) -> dict[str, Any] | None:
        """Retrieve fleet skills for the problem and build the inject overrides.

        Discovery is non-blocking: if the fleet has nothing relevant (auto) or
        --discover forces it, kick off a background process to fetch + index
        matching skills from skills.sh. This run uses whatever is already in
        Qdrant; the new skills are available on the next run."""
        skills = await self._safe_retrieve(prompt)
        # skills is None when retrieval FAILED (e.g., timeout); [] when it
        # succeeded with no matches. Only auto-discover on a genuine empty
        # result — discovering after a timeout re-indexes and piles more load
        # onto the embed daemon, making the next retrieval time out too
        # (self-perpetuating). --discover still forces it.
        if self.discover or (skills == [] and self.config.skills_auto_discover):
            self._spawn_discovery(prompt)
        if not skills:
            self.ui.info("no matching skills retrieved")
            return None
        self.ui.stage(f"injecting {len(skills)} skill(s)")
        for s in skills:
            self.ui.info(f"  {s.title} ({s.score:.2f})")
            self.store.append_skill({"id": s.id, "title": s.title, "score": s.score})
        return {"_skills": [s.to_override() for s in skills]}

    async def _safe_retrieve(self, prompt: str) -> list | None:
        # Run the blocking HTTP retrieval off the event loop and under a live
        # spinner — otherwise the status line freezes for the whole wait (which
        # can be tens of seconds when the indexer's refiner hop is slow).
        # Returns [] on a successful empty result, None on failure (timeout) so
        # the caller can tell "no matches" from "retrieval broke".
        try:
            async with self.ui.working("retrieving skills"):
                return await asyncio.to_thread(self.retriever.retrieve, prompt)
        except Exception as exc:  # noqa: BLE001 — retrieval is best-effort
            self.ui.warn(f"skill retrieval unavailable: {exc}")
            return None

    async def _drive(
        self,
        text: str,
        flags: ModeFlags,
        *,
        phase: str,
        overrides: dict[str, Any] | None = None,
    ) -> DriveResult:
        result = DriveResult()
        ws = WSClient(self.config)
        pending_tool: dict[str, Any] | None = None
        label = {"before": "assessing state", "run": "investigating", "after": "verifying"}.get(phase, "working")

        try:
            async with self.ui.working(label) as work:
                await ws.connect()
                await ws.send(
                    query_message(
                        text,
                        source=self.config.source,
                        overrides=overrides,
                        provider=self.config.provider,
                        read_only=flags.read_only,
                    )
                )

                async for event in ws.events():
                    work.set(_spinner_label(event, label))
                    if phase == "run":
                        self.ui.render(event)
                    # Raw type trace so we can see exactly what the server emits.
                    self.store.append_event({"phase": phase, "type": event.type})

                    if self._pending_file_changes and not event.is_file_diff and not event.is_confirm_request:
                        self._flush_pending_files(result)

                    if event.type == "session.init":
                        result.session_id = event.get("session_id", "")
                    elif event.is_tool_call:
                        pending_tool = self._record_tool_call(event, flags, result)
                    elif event.is_file_diff:
                        self._record_file_diff(event, result)
                    elif event.type == "agent.tool_exec":
                        self._record_tool_exec(event, result)
                    elif event.is_confirm_request:
                        abort = await self._handle_confirm(ws, event, pending_tool, flags, result)
                        pending_tool = None
                        if abort:
                            break
                    elif event.is_secret_request:
                        prompt = event.get("prompt", "")
                        request_id = event.get("request_id", "")
                        with self.ui.suspend():
                            value = ask_secret(prompt)
                        await ws.send(secret_response_message(request_id, value, cancelled=value is None))
                    elif event.type == "agent.result":
                        result.agent_text = event.get("text", "")
                        if event.get("elapsed") is not None:
                            result.elapsed = event.get("elapsed", 0.0)
                        # Don't break: agent.summary (timing/models/tokens) is sent
                        # immediately after on every agent path — capture it too.
                    elif event.type == "agent.summary":
                        result.iterations = event.get("iterations", 0)
                        result.tool_call_count = event.get("tool_calls", 0)
                        result.total_tokens = event.get("total_tokens", 0)
                        result.models = list(event.get("models") or [])
                        if event.get("elapsed") is not None:
                            result.elapsed = event.get("elapsed", result.elapsed)
                        break
                    elif event.type == "agent.error":
                        result.error = event.get("message", "agent error")
                        if not event.get("recoverable"):
                            break
                    elif event.type == "agent.cancelled":
                        result.error = "cancelled"
                        break
        except asyncio.CancelledError:
            try:
                await ws.send(cancel_message())
            except Exception:  # noqa: BLE001
                pass
            raise
        except Exception as exc:  # noqa: BLE001 — surface transport errors as run errors
            result.error = str(exc)
        finally:
            await ws.close()
        return result

    # -- event recording -------------------------------------------------
    def _record_tool_call(self, event: Event, flags: ModeFlags, result: DriveResult) -> dict[str, Any]:
        args = event.get("args", {}) or {}
        command = args.get("command") if isinstance(args, dict) else None
        tier = classify(
            tool_name=event.get("name"),
            command=command,
            guard_threat=event.guard_threat,
            args=args if isinstance(args, dict) else None,
            egress_allow_hosts=self.config.egress_allow_hosts,
        )
        record = {
            "name": event.get("name"),
            "command": command,
            "args": args,  # full tool args (redacted on write) — structured tools have no `command`
            "tier": tier.name.lower(),
            "guard": event.guard_threat,
            "applied": tier <= RiskTier.READ_ONLY,  # gated tools flip to True on confirm
        }
        result.commands.append(record)
        self.store.append_command(record)
        # Activation commands (deploy, restart, rebuild, scp, ...) count as
        # applied even when they run via ssh/shell with no file.diff and no
        # confirm gate. File-editing tools defer applied_any to the confirm
        # gate or the pending-file auto-flush so a denied edit doesn't stick.
        if not flags.read_only and command and _is_activation_command(command):
            result.applied_any = True
        if command and _is_activation_command(command):
            result.edited_since_activation = False  # the edit was followed by an activation
        if tier >= RiskTier.MEDIUM:
            result.risk_notes.append(f"{tier.name.lower()}-risk: {command or event.get('name')}")
        return record

    def _record_file_diff(self, event: Event, result: DriveResult) -> None:
        path = event.get("path")
        pre_hash = event.get("snapshot_id") or event.get("pre_hash")
        # The server emits file.diff twice per code_edit: once for the preview
        # (propose phase) and once from the verified-write result parsing.
        # Skip the duplicate so it doesn't create a spurious pending entry.
        if pre_hash and any(
            c.get("file_path") == path and c.get("snapshot_id") == pre_hash for c in result.file_changes
        ):
            return
        change = {
            "file_path": path,
            "additions": event.get("additions", 0),
            "deletions": event.get("deletions", 0),
            "action": event.get("action", "edited"),
            "pre_hash": event.get("pre_hash"),
            "snapshot_id": pre_hash,
        }
        result.file_changes.append(change)
        self._pending_file_changes.append(change)
        self.store.write_diff_snapshot(event.get("path", "unknown"), event.get("diff_text", ""))
        self.ledger.append(
            ChangeRecord(
                kind="file",
                reason="agent file edit",
                file_path=event.get("path"),
                pre_hash=change["snapshot_id"],
                post_hash=event.get("post_hash"),
                diff_text=event.get("diff_text"),
                action=change["action"],
            )
        )
        if change["snapshot_id"]:
            result.rollback_hints.append(
                f"revert {change['file_path']} via revert_file(pre_hash={change['snapshot_id']})"
            )

    def _flush_pending_files(self, result: DriveResult) -> None:
        """Mark buffered file changes as applied (server auto-approved them)."""
        if self._pending_file_changes:
            result.applied_any = True
            result.edited_since_activation = True
            self._pending_file_changes.clear()

    async def _revert_pending_files(self, result: DriveResult) -> None:
        """Revert all pending file changes and clean up state."""
        denied_paths: set[str] = set()
        for change in self._pending_file_changes:
            path = change.get("file_path", "")
            pre_hash = change.get("snapshot_id") or change.get("pre_hash", "")
            if path and pre_hash:
                await _revert_one(self.config, self.config.source, path, pre_hash)
            denied_paths.add(path)
        result.file_changes = [c for c in result.file_changes if c.get("file_path") not in denied_paths]
        if denied_paths:
            self.ledger.remove_by_paths(denied_paths)
        self._pending_file_changes.clear()

    def _record_tool_exec(self, event: Event, result: DriveResult) -> None:
        if event.get("status") != "done":
            return
        result.observations.append(
            {
                "tool": event.get("name"),
                "output": event.get("output") or "",
                "output_chars": event.get("output_chars"),
                "is_error": event.get("is_error", False),
                "args": event.get("args"),
            }
        )

    async def _handle_confirm(
        self,
        ws: WSClient,
        event: Event,
        pending_tool: dict[str, Any] | None,
        flags: ModeFlags,
        result: DriveResult,
    ) -> bool:
        if event.get("auto_accepted"):
            self._flush_pending_files(result)
            return False

        prompt = event.get("prompt", "")
        request_id = event.get("request_id", "")
        command = pending_tool.get("command") if pending_tool else None
        name = pending_tool.get("name") if pending_tool else None
        guard = pending_tool.get("guard") if pending_tool else None
        tier = classify(
            tool_name=name,
            command=command,
            guard_threat=guard,
            confirm_prompt=prompt,
            args=pending_tool.get("args") if pending_tool else None,
            egress_allow_hosts=self.config.egress_allow_hosts,
        )

        # Sticky "all": once the user chose "all", auto-confirm later non-high-risk
        # prompts locally. Covers confirms emitted in parallel (a model proposing
        # multiple edits at once) before the server's auto-accept flag propagates.
        # HIGH-risk always re-prompts — "all" never silently runs a destructive op.
        if self._auto_accept_all and tier < RiskTier.HIGH:
            await ws.send(confirm_response_message(request_id, True, auto_accept=True))
            if pending_tool is not None:
                pending_tool["applied"] = True
            self._flush_pending_files(result)
            result.applied_any = True
            return False

        gate = evaluate(tier, flags)

        if gate.decision == Decision.APPROVE:
            confirmed, auto_accept = True, gate.auto_accept
        elif gate.decision == Decision.DENY:
            confirmed, auto_accept = False, False
            self.ui.warn(f"denied ({gate.reason}): {prompt}")
        else:  # PROMPT
            with self.ui.suspend():  # stop the spinner so we can read the answer
                confirmed, auto_accept = ask_confirm(prompt, tier, console=self.ui.console)
            if auto_accept:  # user chose "all" — make it sticky for the rest of the run
                self._auto_accept_all = True

        await ws.send(confirm_response_message(request_id, confirmed, auto_accept=auto_accept))
        if pending_tool is not None:
            pending_tool["applied"] = confirmed
        if confirmed:
            self._flush_pending_files(result)
            result.applied_any = True
            return False

        await self._revert_pending_files(result)
        # User denied changes.
        # Cancel the run so the agent can't retry via a different tool
        # (sed, write_file, etc.) and bypass the gate.
        await ws.send(cancel_message())
        return True

    # -- prompt + flag helpers -------------------------------------------
    def _main_text(self, prompt: str) -> str:
        return f"{self.config.default_agent} {prompt}".strip()

    def _probe_text(self, prompt: str) -> str:
        return (
            f"{self.config.probe_agent} [read-only diagnostics only — do not change anything] "
            f"Assess the current state relevant to: {prompt}"
        ).strip()

    def _after_probe_text(self, prompt: str) -> str:
        # Tight, targeted re-check — NOT an open-ended re-investigation. The after
        # probe only needs to confirm whether the original symptom is gone on the
        # exact target; left open it free-roams (reads unrelated/nonexistent files,
        # probes URLs, runs docker_ps) which is slow and can trip error-recovery
        # escalation into a heavy reasoner lane.
        return (
            f"{self.config.probe_agent} [read-only verification — do not change anything] "
            f"Re-check ONLY whether the problem described here is now resolved: {prompt}. "
            "Inspect just the exact target named (the same file, container, service, or host) with at "
            "most two read-only checks. Do NOT read unrelated or other files, explore directories, "
            "probe external URLs, or run broad scans."
        ).strip()

    def _readonly_flags(self) -> ModeFlags:
        return ModeFlags(read_only=True)

    _MAX_ENFORCE = 3  # bounded fix-enforcement re-drives (cheap on flat-fee providers; sequential)

    async def _enforce_fix(
        self, prompt: str, before: DriveResult, main: DriveResult, overrides: dict[str, Any] | None
    ) -> bool:
        """Re-drive the agent toward a working fix when it stopped short, on a
        writable run where the target was actually broken. Returns True if any
        re-drive ran (so verification forces a live re-probe).

        Gated on the BEFORE-state being broken so a healthy host stays a clean
        NOT APPLIED (no nagging). Bounded by _MAX_ENFORCE; each pass progresses
        apply -> activate -> done, or gives up (then verify reports the truth).
        """
        if self.flags.read_only:
            return False
        before_sig = extract_signals(before.observations + [{"output": before.agent_text}])
        target_was_broken = bool(before_sig.unhealthy_hits) or before_sig.bad_http
        if not target_was_broken:
            return False
        enforced = False
        for _ in range(self._MAX_ENFORCE):
            nudge = _enforcement_nudge(target_was_broken, main.applied_any, main.edited_since_activation)
            if nudge is None:
                break
            if nudge == "apply":
                self.ui.warn("target was broken but no fix applied — re-prompting to apply")
                text = self._enforce_fix_text(prompt, main)
            else:  # "activate"
                self.ui.warn("change applied but not activated — re-prompting to activate")
                text = self._activate_text(prompt, main)
            extra = await self._drive(text, self.flags, phase="run", overrides=overrides)
            for obs in extra.observations:
                self.store.append_observation({**obs, "phase": "run"})
            self._merge_drive(main, extra)
            enforced = True
        return enforced

    def _enforce_fix_text(self, prompt: str, main: DriveResult) -> str:
        return (
            f"{self.config.default_agent} You already diagnosed this problem ({prompt}) but only "
            "PROPOSED a fix without applying it, and the target is still failing. This is a writable "
            "run: applying is mandatory. Do NOT re-investigate from scratch — apply the fix you "
            "identified now, activate it (rebuild/redeploy/restart as needed), and confirm the target "
            f"is healthy.\n\nYour prior diagnosis and proposed fix:\n{main.agent_text}"
        ).strip()

    def _activate_text(self, prompt: str, main: DriveResult) -> str:
        files = ", ".join(c.get("file_path", "?") for c in main.file_changes) or "the files you changed"
        return (
            f"{self.config.default_agent} A fix was already applied to {files} for this problem "
            f"({prompt}), but it was NOT activated in the running target, which is still failing. "
            "Do NOT re-diagnose or re-edit. Activate the pending change now — rebuild, redeploy, "
            "restart, reload, or run the project's deploy mechanism as appropriate for the target — "
            "then confirm the target is running the updated change."
        ).strip()

    def _merge_drive(self, main: DriveResult, extra: DriveResult) -> None:
        """Fold an enforcement re-drive (apply/activate pass) back into the main
        result so the report and verdict reflect the whole run."""
        main.commands.extend(extra.commands)
        main.file_changes.extend(extra.file_changes)
        main.observations.extend(extra.observations)
        main.risk_notes.extend(extra.risk_notes)
        main.rollback_hints.extend(extra.rollback_hints)
        main.applied_any = main.applied_any or extra.applied_any
        main.edited_since_activation = extra.edited_since_activation
        main.elapsed += extra.elapsed
        main.iterations += extra.iterations
        main.tool_call_count += extra.tool_call_count
        main.total_tokens += extra.total_tokens
        for m in extra.models:
            if m not in main.models:
                main.models.append(m)
        # Use the re-drive's narrative when it produced a COMPLETE report (parseable
        # VERDICT) — it reflects the post-apply/activate state, so the report body
        # won't contradict the final verdict. Otherwise keep the richer original
        # diagnosis. Either way the verdict itself comes from the forced live
        # re-probe in _verify, so a stale self-verdict can't win.
        if extra.agent_text and parse_verdict(extra.agent_text) is not None:
            main.agent_text = extra.agent_text

    def _fix_plan_md(self, result: DriveResult) -> str:
        lines = ["# Fix Plan", ""]
        for c in result.commands:
            if c.get("tier") != "read_only":
                lines.append(f"- [{c.get('tier')}] {c.get('command') or c.get('name')}")
        for f in result.file_changes:
            lines.append(f"- edit {f.get('file_path')} (+{f.get('additions')} -{f.get('deletions')})")
        return "\n".join(lines) + "\n"
