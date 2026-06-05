"""Felix CLI.

felix "why is container xyz-1 unhealthy?"        # diagnose + fix + verify
felix "..." --dry-run | --apply | --yes | --read-only | --deep
felix doctor                                          # self-check
felix last | explain | undo | replay [RUN_ID]         # work with prior runs
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import click
import httpx

from felix.api.rest import RestClient
from felix.config import Config, load_config
from felix.doctor import run_doctor
from felix.pipeline.orchestrator import Orchestrator
from felix.pipeline.preflight import check as preflight_check
from felix.runstore.reader import load_run
from felix.runstore.store import RunStore
from felix.runstore.undo import plan_undo, undo_run
from felix.safety.gate import ModeFlags
from felix.skills.acquire import acquire, acquire_skill, approve_quarantined
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
@click.version_option(package_name="felix")
def main() -> None:
    """Felix — diagnose. fix. verify."""


@main.command()
@click.argument("prompt", nargs=-1, required=True)
@click.option("--dry-run", is_flag=True, help="Trace the plan; execute nothing.")
@click.option("--apply", "apply_", is_flag=True, help="Auto-apply read-only + low-risk fixes.")
@click.option("--yes", is_flag=True, help="Auto-confirm medium-risk (high-risk still prompts).")
@click.option("--read-only", is_flag=True, help="Diagnose and propose only; block all changes.")
@click.option("--deep", is_flag=True, help="Deeper investigation (more areas/rounds).")
@click.option("--discover", is_flag=True, help="Search skills.sh for task-relevant skills, pull + index them first.")
def run(
    prompt: tuple[str, ...],
    dry_run: bool,
    apply_: bool,
    yes: bool,
    read_only: bool,
    deep: bool,
    discover: bool,
) -> None:
    """Diagnose, fix, and verify the described problem."""
    text = " ".join(prompt).strip()
    config = load_config()
    ui = ConsoleUI()

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
    for r in results:
        ui.console.print(f"[green]{r.installs:>7}[/green]  [bold]{r.ref}[/bold]  [dim]{r.name}[/dim]")
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
    try:
        with tempfile.TemporaryDirectory(prefix="felix-skill-") as tmp:
            for ref in refs:
                source, _, skill_id = ref.partition("@")
                if not skill_id:
                    ui.error(f"expected owner/repo@skill, got: {ref}")
                    continue
                with ui.spinner(f"fetching + vetting {ref}"):
                    out_name, err = acquire_skill(
                        source, skill_id, config.catalog_dir, Path(tmp), config=config, vet_client=vet_client
                    )
                if err:
                    ui.error(f"{ref}: {err}")
                else:
                    ui.success(f"added {ref} -> {out_name}")
                    added += 1
    finally:
        if vet_client is not None:
            vet_client.close()
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
    for name, prov in data.get("skills", {}).items():
        ui.console.print(f"- {prov.get('name', name)}  [dim]{prov.get('repo', '')}[/dim]")


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
