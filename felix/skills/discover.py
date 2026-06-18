"""Dynamic skill discovery against the skills.sh registry.

`GET https://skills.sh/api/search?q=<query>` returns the same ranked results
as `npx skills find` but as clean JSON — no Node dependency, no ANSI parsing.
Each result identifies a skill as `source` (owner/repo) + `skillId` (the skill within
that repo), which `acquire_skill` then pulls from GitHub.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from felix.config import Config
from felix.skills.acquire import acquire_skill, skills_sh_url
from felix.skills.index import build_and_index
from felix.skills.judge_client import build_judge_client

# skills.sh `source` feeds straight into the codeload URL. Pin it to owner/repo
# so a crafted entry can't smuggle path traversal or a second path segment.
_REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")


@dataclass
class FoundSkill:
    id: str  # owner/repo/skill
    source: str  # owner/repo
    skill_id: str  # skill name within the repo
    name: str
    installs: int

    @property
    def ref(self) -> str:
        return f"{self.source}@{self.skill_id}"


def search_skills(
    query: str,
    config: Config,
    *,
    limit: int | None = None,
    client: httpx.Client | None = None,
) -> list[FoundSkill]:
    owns = client is None
    client = client or httpx.Client(timeout=20.0, follow_redirects=True)
    try:
        resp = client.get(config.skills_search_api, params={"q": query})
        resp.raise_for_status()
        data = resp.json()
    finally:
        if owns:
            client.close()
    return parse_results(data, limit=limit or config.skills_discover_limit)


def discover_and_index(config: Config, query: str, *, log: Any = print) -> dict[str, Any]:
    """Search skills.sh for a query, acquire matches into the catalog, and index
    them incrementally. Used both by `felix skills discover` and the orchestrator's
    background discovery process. ``log`` receives one-line progress strings."""
    found = search_skills(query, config)
    if not found:
        log("no new skills found on skills.sh")
        return {"found": 0, "added": 0}

    # Auto-discovered skills are the least-trusted path (no human picked them), so
    # vetting matters most here. Build the judge once for the whole batch.
    vet_client = build_judge_client(config)
    added = 0
    try:
        with tempfile.TemporaryDirectory(prefix="felix-discover-") as tmp:
            for f in found:
                out_name, err = acquire_skill(
                    f.source,
                    f.skill_id,
                    config.catalog_dir,
                    Path(tmp),
                    config=config,
                    vet_client=vet_client,
                    skills_sh_url=skills_sh_url(f.source, f.skill_id),
                )
                if err:
                    log(f"skip {f.ref}: {err}")
                else:
                    log(f"+ {f.ref} ({f.installs} installs)")
                    added += 1
    finally:
        if vet_client is not None:
            vet_client.close()

    result: dict[str, Any] = {"found": len(found), "added": added}
    if added:
        log(f"indexing {added} new skill(s) (incremental)...")
        result["indexer"] = build_and_index(config).indexer_response  # clean=False
        log(f"done: {result['indexer']}")
    return result


def parse_results(data: Any, *, limit: int) -> list[FoundSkill]:
    skills = data.get("skills", []) if isinstance(data, dict) else []
    out: list[FoundSkill] = []
    for s in skills:
        if not isinstance(s, dict):
            continue
        source = str(s.get("source", "")).strip()
        skill_id = str(s.get("skillId") or s.get("name") or "").strip()
        if not source or not skill_id:
            continue
        if not _REPO_RE.match(source):  # drop malformed/injected owner/repo
            continue
        out.append(
            FoundSkill(
                id=str(s.get("id") or f"{source}/{skill_id}"),
                source=source,
                skill_id=skill_id,
                name=str(s.get("name") or skill_id),
                installs=int(s.get("installs", 0) or 0),
            )
        )
        if len(out) >= limit:
            break
    return out
