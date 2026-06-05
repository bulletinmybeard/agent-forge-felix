"""Acquire Agent Skills from GitHub repos and normalize them into the catalog.

An Agent Skill is a folder with a SKILL.md (YAML frontmatter `name`/`description`
+ markdown body). We fetch each source repo as a tarball, discover SKILL.md
files, parse them, and write one normalized markdown file per skill into the
catalog plus a provenance manifest. Network fetch is isolated from parsing so
the parser/normalizer stay unit-testable offline.
"""

from __future__ import annotations

import fnmatch
import io
import json
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

from felix.skills.sources import Source
from felix.skills.vet import VetResult, vet_skill

_FRONTMATTER = re.compile(r"^\s*---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

# Bundled system-ops baseline skills, always added to the catalog so Felix has
# core diagnostic know-how regardless of what external sources provide.
ANCHORS_DIR = Path(__file__).resolve().parent / "anchors"


@dataclass
class Skill:
    name: str
    description: str
    body: str
    frontmatter: dict


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "skill"


def parse_skill_md(text: str) -> Skill:
    """Parse a SKILL.md into name/description/body. Missing frontmatter is
    tolerated — the first heading or filename can stand in for the name."""
    m = _FRONTMATTER.match(text)
    if m:
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            fm = {}
        body = m.group(2).strip()
    else:
        fm = {}
        body = text.strip()
    if not isinstance(fm, dict):
        fm = {}
    name = str(fm.get("name") or _first_heading(body) or "").strip()
    description = str(fm.get("description") or "").strip()
    return Skill(name=name, description=description, body=body, frontmatter=fm)


def _first_heading(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def normalize_skill(skill: Skill, *, fallback_name: str) -> tuple[str, str]:
    """Return (slug, catalog_markdown). The markdown leads with the name and
    description so retrieval embeds the intent, then the full body."""
    name = skill.name or fallback_name
    slug = slugify(name)
    parts = [f"# {name}", ""]
    if skill.description:
        parts += [skill.description, ""]
    parts.append(skill.body)
    return slug, "\n".join(parts).rstrip() + "\n"


# -- discovery within an extracted repo ----------------------------------


def discover_skill_files(root: Path, source: Source) -> list[Path]:
    """Find SKILL.md files under root, honoring the source's subdir + filters."""
    base = root / source.subdir if source.subdir else root
    if not base.exists():
        return []
    found: list[Path] = []
    for path in base.rglob("SKILL.md"):
        rel = str(path.parent.relative_to(base))
        if source.include and not any(fnmatch.fnmatch(rel, p) for p in source.include):
            continue
        if source.exclude and any(fnmatch.fnmatch(rel, p) for p in source.exclude):
            continue
        found.append(path)
    return sorted(found)


# -- network fetch -------------------------------------------------------


def fetch_repo_tarball(repo: str, ref: str = "HEAD", *, client: httpx.Client | None = None) -> bytes:
    """Download a GitHub repo tarball. codeload serves any ref under tar.gz/."""
    url = f"https://codeload.github.com/{repo}/tar.gz/{ref}"
    owns = client is None
    client = client or httpx.Client(timeout=60.0, follow_redirects=True)
    try:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content
    finally:
        if owns:
            client.close()


def extract_tarball(data: bytes, dest: Path) -> Path:
    """Extract a gzip tarball; return the single top-level dir GitHub wraps it in."""
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        members = [m for m in tar.getmembers() if _safe_member(m, dest)]
        # filter="data" is the stdlib guard (3.12+): rejects absolute paths and
        # links escaping dest. _safe_member is belt-and-suspenders on top.
        tar.extractall(dest, members=members, filter="data")
    tops = [p for p in dest.iterdir() if p.is_dir()]
    return tops[0] if len(tops) == 1 else dest


def _safe_member(member: tarfile.TarInfo, dest: Path) -> bool:
    """Guard against path traversal in tar members. Reject links and devices
    outright (a symlink's target isn't covered by the name check), then confirm
    the resolved path stays within dest (is_relative_to, not a prefix match —
    a prefix match accepts a sibling like /x/dest-evil for dest /x/dest)."""
    if member.issym() or member.islnk() or member.isdev():
        return False
    dest = dest.resolve()
    target = (dest / member.name).resolve()
    return target == dest or target.is_relative_to(dest)


# -- orchestration -------------------------------------------------------


@dataclass
class AcquireResult:
    written: list[str]
    skipped: list[str]
    manifest: dict


def _read_manifest(catalog_dir: Path) -> dict:
    p = catalog_dir / "_manifest.json"
    if p.is_file():
        try:
            data = json.loads(p.read_text())
            if isinstance(data, dict) and isinstance(data.get("skills"), dict):
                return data
        except json.JSONDecodeError:
            pass
    return {"skills": {}, "count": 0}


def _write_manifest(catalog_dir: Path, skills: dict[str, dict]) -> None:
    (catalog_dir / "_manifest.json").write_text(json.dumps({"skills": skills, "count": len(skills)}, indent=2))


def _match_skill_file(files: list[Path], skill_id: str) -> Path | None:
    """Pick the SKILL.md for a specific skill id within a multi-skill repo."""
    sid = slugify(skill_id)
    for f in files:  # exact folder-name match is the common case
        if slugify(f.parent.name) == sid:
            return f
    for f in files:  # fall back to frontmatter name slug
        try:
            if slugify(parse_skill_md(f.read_text(encoding="utf-8")).name) == sid:
                return f
        except Exception:  # noqa: BLE001
            continue
    return files[0] if len(files) == 1 else None


def _sidecar(result: VetResult, prov: dict) -> str:
    """Quarantine verdict record written next to the held skill."""
    return json.dumps(
        {
            "verdict": result.verdict,
            "risk": result.risk.name.lower(),
            "findings": [
                {"category": f.category, "severity": f.severity.name.lower(), "line": f.line, "snippet": f.snippet}
                for f in result.findings
            ],
            "llm": (
                {
                    "risk": result.llm.risk.name.lower(),
                    "categories": result.llm.categories,
                    "rationale": result.llm.rationale,
                    "inconclusive": result.llm.inconclusive,
                }
                if result.llm
                else None
            ),
            "provenance": {k: v for k, v in prov.items() if k != "markdown"},
        },
        indent=2,
    )


def route_skill(out_name: str, markdown: str, prov: dict, *, catalog_dir: Path, config=None, client=None) -> str:
    """Vet a normalized skill, then write it to the catalog or hold it in
    quarantine. Returns "written" or "quarantined". Mutates ``prov`` with the
    vet outcome so the manifest stays auditable.

    config=None or skills_vetting=="off" skips vetting (current behavior). "warn"
    records the verdict but still writes to the catalog."""
    if config is None or config.skills_vetting == "off":
        (catalog_dir / out_name).write_text(markdown, encoding="utf-8")
        return "written"

    result = vet_skill(markdown, config, client=client)
    prov["vetted"] = True
    prov["vet_risk"] = result.risk.name.lower()

    if result.verdict == "allow" or config.skills_vetting == "warn":
        (catalog_dir / out_name).write_text(markdown, encoding="utf-8")
        return "written"

    qdir = catalog_dir.parent / "quarantine"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / out_name).write_text(markdown, encoding="utf-8")
    (qdir / f"{out_name}.vet.json").write_text(_sidecar(result, prov))
    return "quarantined"


def approve_quarantined(out_name: str, *, catalog_dir: Path, quarantine_dir: Path) -> bool:
    """Manual override: move a held skill from quarantine into the catalog and
    drop its verdict sidecar. Returns False if the skill isn't in quarantine.
    The caller re-indexes — approval alone doesn't touch Qdrant."""
    held = quarantine_dir / out_name
    if not held.is_file():
        return False
    catalog_dir.mkdir(parents=True, exist_ok=True)
    (catalog_dir / out_name).write_text(held.read_text(encoding="utf-8"), encoding="utf-8")
    held.unlink()
    sidecar = quarantine_dir / f"{out_name}.vet.json"
    if sidecar.is_file():
        sidecar.unlink()
    return True


def acquire_skill(
    source: str,
    skill_id: str,
    catalog_dir: Path,
    workdir: Path,
    *,
    ref: str = "HEAD",
    client: httpx.Client | None = None,
    config=None,
    vet_client=None,
) -> tuple[str | None, str | None]:
    """Pull a single `source` (owner/repo) + `skill_id` into the catalog.

    Returns (out_name, error). Merges into the existing _manifest.json. When
    `config` enables vetting, a failing skill is held in quarantine and NOT added
    to the catalog/manifest (returned as an error so the caller can log it).
    """
    try:
        data = fetch_repo_tarball(source, ref, client=client)
        root = extract_tarball(data, workdir / source.replace("/", "__"))
    except Exception as exc:  # noqa: BLE001
        return None, f"fetch {source}: {exc}"

    files = discover_skill_files(root, Source(repo=source))
    target = _match_skill_file(files, skill_id)
    if target is None:
        return None, f"skill '{skill_id}' not found in {source} ({len(files)} skills present)"

    out_name, prov = _write_skill(target, root, repo=source, ref=ref, slug_prefix=source.replace("/", "__"))
    catalog_dir.mkdir(parents=True, exist_ok=True)
    status = route_skill(
        out_name, prov.pop("markdown"), prov, catalog_dir=catalog_dir, config=config, client=vet_client
    )
    if status == "quarantined":
        return None, f"quarantined '{skill_id}' from {source} (vet_risk={prov.get('vet_risk')})"

    manifest = _read_manifest(catalog_dir)
    manifest["skills"][out_name] = prov
    _write_manifest(catalog_dir, manifest["skills"])
    return out_name, None


def _write_skill(skill_file: Path, root: Path, *, repo: str, ref: str, slug_prefix: str) -> tuple[str, dict]:
    """Normalize one skill markdown into a catalog entry; return (out_name, provenance).

    External skills are folders named after the skill containing SKILL.md, so the
    parent dir names them; bundled anchors are flat `<name>.md` files.
    """
    skill = parse_skill_md(skill_file.read_text(encoding="utf-8"))
    fallback = skill_file.parent.name if skill_file.name == "SKILL.md" else skill_file.stem
    slug, markdown = normalize_skill(skill, fallback_name=fallback)
    out_name = f"{slug_prefix}__{slug}.md"
    prov = {
        "name": skill.name or fallback,
        "description": skill.description,
        "repo": repo,
        "ref": ref,
        "path": str(skill_file.parent.relative_to(root)),
        "markdown": markdown,
    }
    return out_name, prov


def acquire(
    sources: list[Source],
    catalog_dir: Path,
    workdir: Path,
    *,
    client: httpx.Client | None = None,
    anchors_dir: Path | None = ANCHORS_DIR,
    config=None,
    vet_client=None,
) -> AcquireResult:
    """Pull every source, normalize each SKILL.md into the catalog, write
    _manifest.json with provenance (which repo/ref each skill came from).
    Bundled anchor skills are always included first.

    Anchors are first-party and skip vetting. External sources are vetted when
    `config` enables it — a failing skill is quarantined (listed in `skipped`),
    not written to the catalog."""
    catalog_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    skipped: list[str] = []
    provenance: dict[str, dict] = {}

    # Bundled anchors first — flat `<name>.md` files, guaranteed system-ops baseline.
    if anchors_dir and anchors_dir.is_dir():
        for skill_file in sorted(anchors_dir.glob("*.md")):
            try:
                out_name, prov = _write_skill(
                    skill_file, anchors_dir, repo="local:anchors", ref="bundled", slug_prefix="anchor"
                )
                (catalog_dir / out_name).write_text(prov.pop("markdown"), encoding="utf-8")
                written.append(out_name)
                provenance[out_name] = prov
            except Exception as exc:  # noqa: BLE001
                skipped.append(f"anchor:{skill_file}: {exc}")

    for source in sources:
        try:
            data = fetch_repo_tarball(source.repo, source.ref, client=client)
            root = extract_tarball(data, workdir / source.slug)
        except Exception as exc:  # noqa: BLE001 — one bad source shouldn't abort the rest
            skipped.append(f"{source.repo}: {exc}")
            continue

        for skill_file in discover_skill_files(root, source):
            try:
                # Namespace by repo slug to avoid cross-source slug collisions.
                out_name, prov = _write_skill(
                    skill_file, root, repo=source.repo, ref=source.ref, slug_prefix=source.slug
                )
                status = route_skill(
                    out_name, prov.pop("markdown"), prov, catalog_dir=catalog_dir, config=config, client=vet_client
                )
                if status == "quarantined":
                    skipped.append(
                        f"{source.repo}:{skill_file.parent.name}: quarantined (vet_risk={prov.get('vet_risk')})"
                    )
                    continue
                written.append(out_name)
                provenance[out_name] = prov
            except Exception as exc:  # noqa: BLE001
                skipped.append(f"{source.repo}:{skill_file}: {exc}")

    # Merge into any existing manifest so dynamically `add`-ed skills survive.
    merged = _read_manifest(catalog_dir)["skills"]
    merged.update(provenance)
    _write_manifest(catalog_dir, merged)
    return AcquireResult(written=written, skipped=skipped, manifest={"skills": merged, "count": len(merged)})
