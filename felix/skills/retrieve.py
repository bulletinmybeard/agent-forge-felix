"""Runtime skill retrieval.

For a given problem, query the Qdrant-backed search service (scoped to the
felix-skills source) and map the hits into the `overrides._skills` shape the
server's `_build_skill_prompt` injects (each needs `instruction_text`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from felix.api.search import SearchClient
from felix.config import Config

# Cap the smart (cloud query-refinement) hop. When the refiner LLM is slow,
# fail fast and fall back to raw /search (embed + Qdrant, no cloud hop) instead
# of waiting out the full client timeout.
SMART_TIMEOUT = 20.0


@dataclass
class RetrievedSkill:
    id: str
    instruction_text: str
    score: float
    title: str

    def to_override(self) -> dict[str, Any]:
        # Shape consumed by agent-forge _build_skill_prompt (instruction_text /
        # optional condensed). condensed mirrors the full text for follow-ups.
        return {"id": self.id, "instruction_text": self.instruction_text, "condensed": self.instruction_text}


class SkillRetriever:
    def __init__(self, config: Config) -> None:
        self.config = config

    def retrieve(self, problem: str) -> list[RetrievedSkill]:
        with SearchClient(self.config) as client:
            try:
                hits = client.search(
                    problem,
                    smart=True,
                    limit=self.config.skills_top_k,
                    score_threshold=self.config.skills_score_threshold,
                    source_name=self.config.skills_source_name,
                    timeout=SMART_TIMEOUT,
                )
            except Exception:  # noqa: BLE001 — fall back to raw search if smart is slow/fails
                hits = client.search(
                    problem,
                    smart=False,
                    limit=self.config.skills_top_k,
                    score_threshold=self.config.skills_score_threshold,
                    source_name=self.config.skills_source_name,
                )
        return [s for s in (map_hit(h) for h in hits) if s is not None]


def map_hit(hit: dict[str, Any]) -> RetrievedSkill | None:
    """Map a /search result row -> RetrievedSkill. None when there's no text."""
    payload = hit.get("payload", {}) if isinstance(hit, dict) else {}
    text = payload.get("text") or ""
    if not text:
        return None
    doc = payload.get("document_name") or payload.get("source_name") or "skill"
    title = payload.get("section_title") or doc
    return RetrievedSkill(
        id=str(doc),
        instruction_text=str(text),
        score=float(hit.get("score", 0.0)),
        title=str(title),
    )
