"""Felix CLI.

felix "why is container xyz-1 unhealthy?"        # diagnose + fix + verify
felix "..." --dry-run | --apply | --yes | --read-only | --deep
felix doctor                                          # self-check
felix last | explain | undo | replay [RUN_ID]         # work with prior runs
felix permissions show | set-mode | allow | …         # shell/SSH policy overrides
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import click
import httpx
from chalkbox import Table as CTable

from felix.api.rest import RestClient
from felix.config import Config, load_config
from felix.doctor import run_doctor
from felix.permissions import (
    empty_policy,
    format_policy_block,
    merge_list_edit,
    mode_warning,
    normalize_policy,
    overrides_put_body,
    set_mode,
)
from felix.pipeline.orchestrator import Orchestrator
from felix.pipeline.preflight import check as preflight_check
from felix.runstore.reader import load_run
from felix.runstore.store import RunStore
from felix.runstore.undo import plan_undo, undo_run
from felix.safety.gate import ModeFlags
from felix.skills.acquire import acquire, acquire_skill, approve_quarantined, skills_sh_url
from felix.skills.discover import discover_and_index, search_skills
from felix.skills.index import build_and_index
from felix.skills.judge_client import build_judge_client
from felix.skills.retrieve import SkillRetriever
from felix.skills.sources import load_sources
from felix.skills.vet import vet_skill
from felix.ui.console import ConsoleUI


class DefaultGroup(click.Group):
    """Group that falls back to the `run` command for a bare prompt arg."""

    def resolve_command(self, ctx: click.Context, args: list[str]):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            # First token isn't a known subcommand — treat the whole thing as a prompt.
            return "run", self.get_command(ctx, "run"), args


@click.group(cls=DefaultGroup, invoke_without_command=False)
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Increase output verbosity (-v, -vv). Also accepted on `run`.",
)
@click.version_option(package_name="felix")
@click.pass_context
def main(ctx: click.Context, verbose: int) -> None:
    """Felix — diagnose. fix. verify."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


@main.command()
@click.argument("prompt", nargs=-1, required=True)
@click.option("--dry-run", is_flag=True, help="Trace the plan; execute nothing.")
@click.option("--apply", "apply_", is_flag=True, help="Auto-apply read-only + low-risk fixes.")
@click.option("--yes", is_flag=True, help="Auto-confirm medium-risk (high-risk still prompts).")
@click.option("--read-only", is_flag=True, help="Diagnose and propose only; block all changes.")
@click.option("--deep", is_flag=True, help="Deeper investigation (more areas/rounds).")
@click.option("--discover", is_flag=True, help="Search skills.sh for task-relevant skills, pull + index them first.")
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Increase output verbosity: -v more detail, -vv tool outputs/iterations.",
)
@click.pass_context
def run(
    ctx: click.Context,
    prompt: tuple[str, ...],
    dry_run: bool,
    apply_: bool,
    yes: bool,
    read_only: bool,
    deep: bool,
    discover: bool,
    verbose: int,
) -> None:
    """Diagnose, fix, and verify the described problem."""
    text = " ".join(prompt).strip()
    config = load_config()
    # Group-level -v plus run-level -v stack (max of both).
    group_v = int((ctx.obj or {}).get("verbose") or 0)
    verbosity = max(verbose, group_v)
    ui = ConsoleUI(verbosity=verbosity)

    pf = preflight_check(text)
    if not pf.ok:
        if pf.kind == "out_of_scope":
            ui.error(f"out of scope: {pf.reason}")
            ui.info(
                'rephrase as a problem to diagnose (e.g., "why can\'t I scp to the server?"), or use a general assistant for how-tos.'
            )
        else:
            ui.error(f"need more information: {pf.reason}")
            ui.info("re-run with a concrete target (path, container, host, or URL).")
        sys.exit(2)

    flags = ModeFlags(read_only=read_only, dry_run=dry_run, apply=apply_, yes=yes, deep=deep)
    ui.banner(text)
    store = RunStore(config.runs_dir).create()
    orch = Orchestrator(config, flags, ui, store, discover=discover)
    try:
        verdict = asyncio.run(orch.run(text))
    except KeyboardInterrupt:
        ui.warn("interrupted")
        sys.exit(130)
    ui.console.print(f"\n[bold]{verdict.value}[/bold]")


@main.command()
def doctor() -> None:
    """Check the Felix <> agent-forge integration."""
    config = load_config()
    ui = ConsoleUI()
    ui.banner("doctor")
    failed = 0
    for chk in run_doctor(config):
        if chk.ok:
            ui.success(f"{chk.name}: {chk.detail}")
        else:
            ui.error(f"{chk.name}: {chk.detail}")
            failed += 1
    sys.exit(1 if failed else 0)


# ── Command permissions (AgentForge ≥ 0.12) ─────────────────────────────


def _perm_tool_option(default: str = "shell"):
    return click.option(
        "--tool",
        type=click.Choice(["shell", "ssh"], case_sensitive=False),
        default=default,
        show_default=True,
        help="Which tool policy to edit.",
    )


def _fetch_tool_bundle(rest: RestClient, tool: str) -> dict:
    data = rest.command_permissions()
    if not isinstance(data, dict) or tool not in data:
        raise click.ClickException(
            f"permissions API missing tool={tool!r} — need AgentForge ≥ 0.12 (/api/permissions/commands)"
        )
    return data[tool] if isinstance(data[tool], dict) else {}


def _current_override(rest: RestClient, tool: str) -> dict:
    """Override document for *tool*, or empty policy when unset."""
    try:
        ov = rest.get_command_overrides()
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            raise click.ClickException("permissions overrides API missing — need AgentForge ≥ 0.12") from exc
        raise click.ClickException(f"permissions API error: {exc}") from exc
    if not isinstance(ov, dict):
        return empty_policy()
    raw = ov.get(tool)
    return normalize_policy(raw if isinstance(raw, dict) else None)


def _put_override(rest: RestClient, tool: str, policy: dict, ui: ConsoleUI, *, dry_run: bool) -> None:
    body = overrides_put_body(tool, policy)  # type: ignore[arg-type]
    if dry_run:
        ui.info(f"dry-run PUT body:\n{json.dumps(body, indent=2)}")
        return
    try:
        rest.put_command_overrides(body)
    except httpx.HTTPStatusError as exc:
        raise click.ClickException(f"PUT overrides failed: {exc}") from exc
    ui.success(f"saved {tool} runtime override")
    warn = mode_warning(str(policy.get("mode", "confirm")))
    if warn:
        ui.warn(warn)
    # Re-fetch effective for display
    try:
        bundle = _fetch_tool_bundle(rest, tool)
        ui.console.print(format_policy_block("effective:", bundle.get("effective")))
    except Exception:  # noqa: BLE001
        ui.console.print(format_policy_block("override:", policy))


@main.group("permissions")
def permissions_group() -> None:
    """Manage AgentForge shell/SSH command permission overrides (runtime)."""


@permissions_group.command("show")
@click.option(
    "--tool",
    type=click.Choice(["shell", "ssh", "all"], case_sensitive=False),
    default="all",
    show_default=True,
)
@click.option("--json", "as_json", is_flag=True, help="Print raw API JSON.")
def permissions_show(tool: str, as_json: bool) -> None:
    """Show YAML baseline, runtime override, and effective policy."""
    config = load_config()
    ui = ConsoleUI()
    with RestClient(config) as rest:
        try:
            data = rest.command_permissions()
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                ui.error("permissions API missing — need AgentForge ≥ 0.12")
                sys.exit(1)
            ui.error(f"permissions API error: {exc}")
            sys.exit(1)
    if as_json:
        if tool != "all" and isinstance(data, dict):
            ui.console.print_json(data=data.get(tool))
        else:
            ui.console.print_json(data=data)
        return
    tools = ["shell", "ssh"] if tool == "all" else [tool]
    for t in tools:
        bundle = data.get(t, {}) if isinstance(data, dict) else {}
        if not isinstance(bundle, dict):
            continue
        ui.info(f"── {t} ──")
        ui.console.print(format_policy_block("yaml (baseline):", bundle.get("yaml")))
        ov = bundle.get("override")
        ui.console.print(format_policy_block("override:", ov if ov else None))
        if not ov:
            ui.console.print("  [dim](no runtime override — YAML only)[/dim]")
        ui.console.print(format_policy_block("effective:", bundle.get("effective")))
        warn = mode_warning(str((bundle.get("effective") or {}).get("mode", "confirm")))
        if warn:
            ui.warn(warn)


@permissions_group.command("set-mode")
@click.argument("mode", type=click.Choice(["confirm", "allowlist", "denylist"], case_sensitive=False))
@_perm_tool_option()
@click.option("--dry-run", is_flag=True, help="Print PUT body only.")
def permissions_set_mode(mode: str, tool: str, dry_run: bool) -> None:
    """Set the runtime override mode (confirm | allowlist | denylist)."""
    config = load_config()
    ui = ConsoleUI()
    with RestClient(config) as rest:
        policy = set_mode(_current_override(rest, tool), mode)  # type: ignore[arg-type]
        _put_override(rest, tool, policy, ui, dry_run=dry_run)


@permissions_group.command("allow")
@click.argument("commands", nargs=-1, required=True)
@_perm_tool_option()
@click.option("--dry-run", is_flag=True, help="Print PUT body only.")
def permissions_allow(commands: tuple[str, ...], tool: str, dry_run: bool) -> None:
    """Append command first-words to allowed_commands (allowlist mode)."""
    config = load_config()
    ui = ConsoleUI()
    with RestClient(config) as rest:
        policy = merge_list_edit(
            _current_override(rest, tool),
            add={"allowed_commands": list(commands)},
        )
        _put_override(rest, tool, policy, ui, dry_run=dry_run)


@permissions_group.command("allow-pattern")
@click.argument("patterns", nargs=-1, required=True)
@_perm_tool_option()
@click.option("--dry-run", is_flag=True, help="Print PUT body only.")
def permissions_allow_pattern(patterns: tuple[str, ...], tool: str, dry_run: bool) -> None:
    """Append regexes to allowed_patterns (allowlist mode)."""
    config = load_config()
    ui = ConsoleUI()
    with RestClient(config) as rest:
        policy = merge_list_edit(
            _current_override(rest, tool),
            add={"allowed_patterns": list(patterns)},
        )
        _put_override(rest, tool, policy, ui, dry_run=dry_run)


@permissions_group.command("deny-pattern")
@click.argument("patterns", nargs=-1, required=True)
@_perm_tool_option()
@click.option("--dry-run", is_flag=True, help="Print PUT body only.")
def permissions_deny_pattern(patterns: tuple[str, ...], tool: str, dry_run: bool) -> None:
    """Append regexes to blocked_patterns (hard deny in all modes)."""
    config = load_config()
    ui = ConsoleUI()
    with RestClient(config) as rest:
        policy = merge_list_edit(
            _current_override(rest, tool),
            add={"blocked_patterns": list(patterns)},
        )
        _put_override(rest, tool, policy, ui, dry_run=dry_run)


@permissions_group.command("remove")
@click.argument("items", nargs=-1, required=True)
@click.option(
    "--from",
    "from_list",
    type=click.Choice(["commands", "allowed-patterns", "blocked-patterns"], case_sensitive=False),
    default="commands",
    show_default=True,
    help="Which list to remove from.",
)
@_perm_tool_option()
@click.option("--dry-run", is_flag=True, help="Print PUT body only.")
def permissions_remove(items: tuple[str, ...], from_list: str, tool: str, dry_run: bool) -> None:
    """Remove exact entries from an allow/block list."""
    key_map = {
        "commands": "allowed_commands",
        "allowed-patterns": "allowed_patterns",
        "blocked-patterns": "blocked_patterns",
    }
    key = key_map[from_list]
    config = load_config()
    ui = ConsoleUI()
    with RestClient(config) as rest:
        policy = merge_list_edit(
            _current_override(rest, tool),
            remove={key: list(items)},
        )
        _put_override(rest, tool, policy, ui, dry_run=dry_run)


@permissions_group.command("reset")
@_perm_tool_option()
@click.option("--all", "reset_all", is_flag=True, help="Delete shell and ssh overrides.")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def permissions_reset(tool: str, reset_all: bool, yes: bool) -> None:
    """Delete the runtime override (fall back to YAML baseline)."""
    config = load_config()
    ui = ConsoleUI()
    target = None if reset_all else tool
    label = "shell+ssh" if reset_all else tool
    if not yes and not click.confirm(f"delete runtime override for {label}?", default=False):
        ui.info("aborted")
        return
    with RestClient(config) as rest:
        try:
            result = rest.delete_command_overrides(target)
        except httpx.HTTPStatusError as exc:
            ui.error(f"DELETE failed: {exc}")
            sys.exit(1)
    ui.success(f"reset {label} (deleted={result.get('deleted', '?')})")


@permissions_group.command("check")
@click.argument("command")
@_perm_tool_option()
def permissions_check(command: str, tool: str) -> None:
    """Validate a command against the effective policy (dry evaluate)."""
    config = load_config()
    ui = ConsoleUI()
    with RestClient(config) as rest:
        try:
            verdict = rest.validate_command(tool, command)
        except httpx.HTTPStatusError as exc:
            ui.error(f"validate failed: {exc}")
            sys.exit(1)
    action = str(verdict.get("action", "?"))
    reason = verdict.get("reason", "")
    source = verdict.get("source", "")
    if action == "allow":
        ui.success(f"{action} ({source}): {reason}")
    elif action == "deny":
        ui.error(f"{action} ({source}): {reason}")
        sys.exit(2)
    else:
        ui.warn(f"{action} ({source}): {reason}")


@permissions_group.group("profile")
def permissions_profile() -> None:
    """Named permission presets (tight / open / user-saved)."""


@permissions_profile.command("list")
@click.option("--json", "as_json", is_flag=True)
def permissions_profile_list(as_json: bool) -> None:
    """List YAML builtins and user-saved profiles."""
    config = load_config()
    ui = ConsoleUI()
    with RestClient(config) as rest:
        try:
            data = rest.list_permission_profiles()
        except httpx.HTTPStatusError as exc:
            ui.error(f"profiles API error: {exc} (need AgentForge with profile support)")
            sys.exit(1)
    if as_json:
        ui.console.print_json(data=data)
        return
    active = data.get("active_profile_id")
    if active:
        ui.info(f"active: {active}")
    for p in data.get("profiles") or []:
        if not isinstance(p, dict):
            continue
        src = p.get("source") or ("yaml" if p.get("builtin") else "user")
        desc = p.get("description") or ""
        mark = " *" if p.get("id") == active else ""
        ui.console.print(f"  [cyan]{p.get('id')}[/cyan]{mark}  [dim]({src})[/dim]  {desc}")


@permissions_profile.command("show")
@click.argument("profile_id")
@click.option("--json", "as_json", is_flag=True)
def permissions_profile_show(profile_id: str, as_json: bool) -> None:
    """Show one profile document."""
    config = load_config()
    ui = ConsoleUI()
    with RestClient(config) as rest:
        try:
            data = rest.get_permission_profile(profile_id)
        except httpx.HTTPStatusError as exc:
            ui.error(f"profile {profile_id!r}: {exc}")
            sys.exit(1)
    if as_json:
        ui.console.print_json(data=data)
        return
    ui.info(f"{data.get('id')} ({data.get('source')})")
    if data.get("description"):
        ui.console.print(f"  {data['description']}")
    if data.get("shell"):
        ui.console.print(format_policy_block("shell:", data["shell"]))
    if data.get("ssh"):
        ui.console.print(format_policy_block("ssh:", data["ssh"]))


@permissions_profile.command("apply")
@click.argument("profile_id")
def permissions_profile_apply(profile_id: str) -> None:
    """Apply a profile as the live runtime override.

    Special ids: ``__yaml__`` (config baseline only), ``__blank__`` (empty lists).
    """
    config = load_config()
    ui = ConsoleUI()
    with RestClient(config) as rest:
        try:
            result = rest.apply_permission_profile(profile_id)
        except httpx.HTTPStatusError as exc:
            ui.error(f"apply failed: {exc}")
            sys.exit(1)
    ui.success(f"applied profile {profile_id}")
    applied = result.get("applied") or {}
    if applied.get("description"):
        ui.info(applied["description"])
    # effective nested shape: effective.shell.effective.mode
    shell_eff = ((result.get("effective") or {}).get("shell") or {}).get("effective") or {}
    warn = mode_warning(str(shell_eff.get("mode", "confirm")))
    if warn:
        ui.warn(warn)
    if shell_eff:
        ui.console.print(format_policy_block("shell effective:", shell_eff))


@permissions_profile.command("save")
@click.argument("profile_id")
@click.option("--description", default="", help="Human label.")
@click.option(
    "--from-current/--empty",
    default=True,
    help="Snapshot current runtime overrides (default) or require explicit empty shell.",
)
def permissions_profile_save(profile_id: str, description: str, from_current: bool) -> None:
    """Save a user profile (from current overrides by default)."""
    config = load_config()
    ui = ConsoleUI()
    body: dict = {
        "description": description or f"felix save {profile_id}",
        "from_current_overrides": from_current,
    }
    with RestClient(config) as rest:
        try:
            result = rest.save_permission_profile(profile_id, body)
        except httpx.HTTPStatusError as exc:
            ui.error(f"save failed: {exc}")
            sys.exit(1)
    ui.success(f"saved user profile {profile_id}")
    if result.get("profile"):
        ui.console.print_json(data=result["profile"])


@permissions_profile.command("delete")
@click.argument("profile_id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def permissions_profile_delete(profile_id: str, yes: bool) -> None:
    """Delete a user-saved profile (not YAML builtins)."""
    config = load_config()
    ui = ConsoleUI()
    if not yes and not click.confirm(f"delete user profile {profile_id}?", default=False):
        ui.info("aborted")
        return
    with RestClient(config) as rest:
        try:
            rest.delete_permission_profile(profile_id)
        except httpx.HTTPStatusError as exc:
            ui.error(f"delete failed: {exc}")
            sys.exit(1)
    ui.success(f"deleted {profile_id}")


def _resolve_run(config: Config, run_id: str | None, ui: ConsoleUI):
    # Accept "last" as an alias for the newest run.
    rid = None if run_id in (None, "last") else run_id
    view = load_run(config.runs_dir, rid)
    if view is None:
        ui.error("no runs found")
        sys.exit(1)
    return view


@main.command()
@click.argument("run_id", required=False)
def last(run_id: str | None) -> None:
    """Show the report for the latest run (or RUN_ID)."""
    config = load_config()
    ui = ConsoleUI()
    view = _resolve_run(config, run_id, ui)
    ui.info(f"run {view.run_id}")
    ui.markdown(view.report or "(no report)")


@main.command()
@click.argument("run_id", required=False)
def explain(run_id: str | None) -> None:
    """Explain the root cause + evidence of a run."""
    config = load_config()
    ui = ConsoleUI()
    view = _resolve_run(config, run_id, ui)
    ui.info(f"run {view.run_id}")
    ui.markdown(view.plan or "(no plan)")
    meta = view.meta
    ui.info(f"verdict: {meta.get('verdict', '?')} | applied: {meta.get('applied', '?')}")
    for obs in view.observations:
        if obs.get("is_error"):
            ui.warn(f"{obs.get('tool')}: {str(obs.get('output'))[:200]}")


@main.command()
@click.argument("run_id", required=False)
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def undo(run_id: str | None, yes: bool) -> None:
    """Revert a run's file changes via revert_file."""
    config = load_config()
    ui = ConsoleUI()
    view = _resolve_run(config, run_id, ui)
    files, manual = plan_undo(view)
    if not files and not manual:
        ui.info("nothing to undo")
        return
    ui.info(f"run {view.run_id}: {len(files)} file change(s) revertible, {len(manual)} manual")
    if not yes and not click.confirm("revert file changes now?", default=False):
        ui.info("aborted")
        return
    outcome = undo_run(config, view, config.default_agent)
    for p in outcome.reverted:
        ui.success(f"reverted {p}")
    for p in outcome.failed:
        ui.error(f"failed to revert {p}")
    for h in outcome.manual:
        ui.warn(f"manual rollback needed: {h}")


@main.command()
@click.argument("run_id", required=False)
@click.option("--dry-run", is_flag=True, default=True, help="Replay commands through /api/dry-run (default).")
def replay(run_id: str | None, dry_run: bool) -> None:
    """Replay a run's commands through the dry-run pipeline."""
    config = load_config()
    ui = ConsoleUI()
    view = _resolve_run(config, run_id, ui)
    cmds = [c for c in view.commands if c.get("command")]
    if not cmds:
        ui.info("no commands to replay")
        return
    with RestClient(config) as rest:
        for c in cmds:
            try:
                trace = rest.dry_run(str(c.get("command")))
                ui.info(f"[{c.get('tier')}] {c.get('command')} -> mode {trace.get('final_mode', '?')}")
            except Exception as exc:  # noqa: BLE001
                ui.error(f"{c.get('command')}: {exc}")


@main.group()
def skills() -> None:
    """Manage the skill fleet (acquire, index, retrieve)."""


@skills.command("pull")
def skills_pull() -> None:
    """Acquire Agent Skills from the sources manifest into the catalog."""
    config = load_config()
    ui = ConsoleUI()
    sources = load_sources()
    if not sources:
        ui.error("no sources configured (felix/skills/sources.yaml)")
        sys.exit(1)
    ui.info(f"pulling {len(sources)} source(s) -> {config.catalog_dir}")
    vet_client = build_judge_client(config)
    if config.skills_vetting != "off" and vet_client is None:
        ui.info("vetting: static scan only (no vetting_provider configured)")
    try:
        with tempfile.TemporaryDirectory(prefix="felix-skills-") as tmp, ui.spinner("fetching + vetting skills"):
            result = acquire(sources, config.catalog_dir, Path(tmp), config=config, vet_client=vet_client)
    finally:
        if vet_client is not None:
            vet_client.close()
    ui.success(f"{len(result.written)} skill(s) written to catalog")
    for s in result.skipped:
        ui.warn(f"skipped: {s}")


@skills.command("index")
@click.option(
    "--clean", is_flag=True, help="Full wipe+rebuild (deletes existing points first). Default is incremental."
)
def skills_index(clean: bool) -> None:
    """Chunk the catalog and index it into Qdrant (incremental by default)."""
    config = load_config()
    ui = ConsoleUI()
    if not config.catalog_dir.is_dir() or not any(config.catalog_dir.glob("*.md")):
        ui.error(f"catalog empty — run `felix skills pull` first ({config.catalog_dir})")
        sys.exit(1)

    mode = "full rebuild" if clean else "incremental"
    ui.info(f"uploading + indexing {config.catalog_dir} ({mode})")
    try:
        with ui.spinner("uploading + indexing (embedding continues server-side)"):
            result = build_and_index(config, clean=clean)
        ui.success(f"{result.chunks} chunk(s) uploaded; indexer: {result.indexer_response}")
    except httpx.TimeoutException:
        ui.warn("upload submitted — the server is still embedding in the background.")
        ui.info('check progress with `felix skills retrieve "<query>"` shortly, or the Qdrant point count.')
    except httpx.HTTPError as exc:
        ui.error(f"indexing request failed: {exc}")
        sys.exit(1)


@skills.command("find")
@click.argument("query", nargs=-1, required=True)
@click.option("--limit", default=10, help="Max results.")
def skills_find(query: tuple[str, ...], limit: int) -> None:
    """Search the skills.sh registry for skills matching a query."""
    config = load_config()
    ui = ConsoleUI()
    with ui.spinner("searching skills.sh"):
        results = search_skills(" ".join(query), config, limit=limit)
    if not results:
        ui.info("no skills found")
        return

    manifest_file = config.catalog_dir / "_manifest.json"
    acquired: set[tuple[str, str]] = set()
    if manifest_file.is_file():
        for prov in json.loads(manifest_file.read_text()).get("skills", {}).values():
            acquired.add((prov.get("repo", ""), prov.get("name", "")))

    table = CTable(headers=["Installs", "Skill", "Added", "Source"], row_styles="severity")
    for r in results:
        url = skills_sh_url(r.source, r.skill_id)
        have = (r.source, r.skill_id) in acquired
        table.add_row(
            str(r.installs),
            r.ref,
            "[green]y[/green]" if have else "[dim]n[/dim]",
            f"[dim][link={url}]skills.sh[/link][/dim]",
            severity="success" if have else "muted",
        )
    ui.console.print(table)
    ui.info("add one with: felix skills add <owner/repo@skill>")


@skills.command("add")
@click.argument("refs", nargs=-1, required=True)
@click.option("--index", "do_index", is_flag=True, help="Index the catalog into Qdrant after adding.")
def skills_add(refs: tuple[str, ...], do_index: bool) -> None:
    """Pull specific skills (owner/repo@skill) into the catalog."""
    config = load_config()
    ui = ConsoleUI()
    vet_client = build_judge_client(config)
    if config.skills_vetting != "off" and vet_client is None:
        ui.info("vetting: static scan only (no vetting_provider configured)")

    added = 0
    rows: list[tuple[str, str, str, str]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="felix-skill-") as tmp:
            for ref in refs:
                source, _, skill_id = ref.partition("@")
                if not skill_id:
                    rows.append((ref, "", "invalid format", "expected owner/repo@skill"))
                    continue
                with ui.spinner(f"fetching + vetting {ref}"):
                    out_name, err = acquire_skill(
                        source,
                        skill_id,
                        config.catalog_dir,
                        Path(tmp),
                        config=config,
                        vet_client=vet_client,
                        skills_sh_url=skills_sh_url(source, skill_id),
                    )
                if err:
                    rows.append((ref, "", "failed", err))
                else:
                    url = skills_sh_url(source, skill_id)
                    rows.append((ref, out_name or "", "added", f"[link={url}]skills.sh[/link]"))
                    added += 1
    finally:
        if vet_client is not None:
            vet_client.close()

    table = CTable(headers=["Skill", "Catalog Entry", "Status", "Details"], row_styles="severity")
    for ref, entry, status, details in rows:
        severity = "success" if status == "added" else "error"
        table.add_row(ref, entry, status, details, severity=severity)
    ui.console.print(table)

    if added and do_index:
        with ui.spinner("uploading + embedding chunks (server-side, can take minutes)"):
            result = build_and_index(config)
        ui.success(f"indexed: {result.indexer_response}")


@skills.command("discover")
@click.argument("query", nargs=-1, required=True)
def skills_discover(query: tuple[str, ...]) -> None:
    """Search skills.sh for a query, pull matches, and index them (incremental)."""
    config = load_config()
    ui = ConsoleUI()
    q = " ".join(query)
    try:
        with ui.spinner(f"discovering + indexing skills for: {q}"):
            result = discover_and_index(config, q, log=ui.info)
        ui.success(f"discovery done: {result.get('added', 0)} added of {result.get('found', 0)} found")
    except httpx.TimeoutException:
        ui.warn("skills pulled — the server is still embedding them in the background.")
    except httpx.HTTPError as exc:
        ui.error(f"discovery request failed: {exc}")
        sys.exit(1)


@skills.command("retrieve")
@click.argument("query", nargs=-1, required=True)
def skills_retrieve(query: tuple[str, ...]) -> None:
    """Preview which skills would be injected for a problem."""
    config = load_config()
    ui = ConsoleUI()
    with ui.spinner("searching the fleet"):
        results = SkillRetriever(config).retrieve(" ".join(query))
    if not results:
        ui.info("no skills matched")
        return
    for s in results:
        ui.console.print(f"[green]{s.score:.3f}[/green]  {s.title}  [dim]({s.id})[/dim]")


@skills.command("list")
def skills_list() -> None:
    """List the acquired catalog with provenance."""
    config = load_config()
    ui = ConsoleUI()
    manifest = config.catalog_dir / "_manifest.json"
    if not manifest.is_file():
        ui.info("catalog empty — run `felix skills pull`")
        return
    data = json.loads(manifest.read_text())
    ui.info(f"{data.get('count', 0)} skill(s) in {config.catalog_dir}")

    table = CTable(headers=["Skill", "Repo", "Source"])
    for name, prov in data.get("skills", {}).items():
        repo = prov.get("repo", "")
        sh_url = prov.get("skills_sh_url", "")
        source = f"[link={sh_url}]skills.sh[/link]" if sh_url else ""
        table.add_row(prov.get("name", name), repo, source)
    ui.console.print(table)


@skills.command("quarantine")
@click.argument("name", required=False)
def skills_quarantine(name: str | None) -> None:
    """List quarantined skills, or show one skill's findings (NAME)."""
    config = load_config()
    ui = ConsoleUI()
    qdir = config.quarantine_dir
    sidecars = sorted(qdir.glob("*.vet.json")) if qdir.is_dir() else []
    if not sidecars:
        ui.info(f"quarantine empty ({qdir})")
        return
    if name is None:
        ui.info(f"{len(sidecars)} skill(s) quarantined in {qdir}")
        for sc in sidecars:
            data = json.loads(sc.read_text())
            skill = sc.name[: -len(".vet.json")]
            cats = ",".join(sorted({f["category"] for f in data.get("findings", [])})) or "-"
            ui.console.print(f"- [bold]{skill}[/bold]  [red]{data.get('risk', '?')}[/red]  [dim]{cats}[/dim]")
        ui.info("inspect: felix skills quarantine <name>  |  release: felix skills approve <name>")
        return
    sc = qdir / f"{name}.vet.json"
    if not sc.is_file():
        ui.error(f"not quarantined: {name}")
        sys.exit(1)
    ui.console.print(sc.read_text())


@skills.command("approve")
@click.argument("name")
@click.option("--index", "do_index", is_flag=True, help="Index the catalog after approving.")
def skills_approve(name: str, do_index: bool) -> None:
    """Release a quarantined skill (NAME) into the catalog (manual override)."""
    config = load_config()
    ui = ConsoleUI()
    if not approve_quarantined(name, catalog_dir=config.catalog_dir, quarantine_dir=config.quarantine_dir):
        ui.error(f"not quarantined: {name}")
        sys.exit(1)
    ui.success(f"approved {name} -> catalog")
    if do_index:
        with ui.spinner("uploading + indexing"):
            result = build_and_index(config)
        ui.success(f"indexed: {result.indexer_response}")


@skills.command("vet")
@click.argument("target")
def skills_vet(target: str) -> None:
    """Vet a skill without acquiring it: a local SKILL.md path or owner/repo@skill."""
    config = load_config()
    ui = ConsoleUI()

    path = Path(target)
    if path.is_file():
        markdown = path.read_text(encoding="utf-8")
    elif "@" in target:
        source, _, skill_id = target.partition("@")
        with tempfile.TemporaryDirectory(prefix="felix-vet-") as tmp:
            # config=None -> fetch + normalize without vetting; we vet the result below.
            out, err = acquire_skill(source, skill_id, Path(tmp) / "cat", Path(tmp) / "work")
            if err:
                ui.error(err)
                sys.exit(1)
            markdown = (Path(tmp) / "cat" / out).read_text(encoding="utf-8")
    else:
        ui.error("expected a local file path or owner/repo@skill")
        sys.exit(1)

    vet_client = build_judge_client(config)
    try:
        result = vet_skill(markdown, config, client=vet_client)
    finally:
        if vet_client is not None:
            vet_client.close()

    marker = ui.success if result.verdict == "allow" else ui.warn
    marker(f"verdict: {result.verdict}  (risk={result.risk.name.lower()})")
    for f in result.findings:
        ui.console.print(f"  [red]{f.category}[/red] L{f.line}: [dim]{f.snippet}[/dim]")
    if result.llm is not None:
        tag = "inconclusive" if result.llm.inconclusive else result.llm.risk.name.lower()
        ui.info(f"  judge: {tag} — {result.llm.rationale}")
    elif config.vetting_provider:
        ui.info("  judge: skipped (hard static hit)")
    else:
        ui.info("  judge: disabled (no vetting_provider) — static scan only")


if __name__ == "__main__":
    main()
