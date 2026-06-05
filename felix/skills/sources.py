"""Skill source manifest.

`sources.yaml` lists the GitHub repos (and skills.sh entries, which are
git-backed) to pull Agent Skills from. Each source can scope which paths to
include/exclude so a large repo contributes only its relevant skills.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Bundled with the package so `felix skills pull` works out of the box.
DEFAULT_MANIFEST = Path(__file__).resolve().parent / "sources.yaml"


@dataclass
class Source:
    repo: str  # "owner/name"
    ref: str = "HEAD"  # branch, tag, or commit
    subdir: str = ""  # restrict discovery to this path within the repo
    include: list[str] = field(default_factory=list)  # glob filters on skill dir
    exclude: list[str] = field(default_factory=list)
    enabled: bool = True

    @property
    def slug(self) -> str:
        return self.repo.replace("/", "__")


def load_sources(path: Path | None = None) -> list[Source]:
    manifest = path or DEFAULT_MANIFEST
    if not manifest.is_file():
        return []
    data = yaml.safe_load(manifest.read_text()) or {}
    raw = data.get("sources", [])
    sources: list[Source] = []
    for item in raw:
        if not isinstance(item, dict) or "repo" not in item:
            continue
        known = {f for f in Source.__dataclass_fields__}
        sources.append(Source(**{k: v for k, v in item.items() if k in known}))
    return [s for s in sources if s.enabled]
