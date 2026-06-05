"""Chunk the skill catalog and index it into Qdrant over HTTP.

Each catalog markdown file becomes one document-section chunk (skills are
self-contained units we retrieve and inject whole). The chunks are uploaded to
agent-forge's `POST /indexer/upload/{source}` endpoint, which writes them
server-side and indexes them — so indexing works from anywhere, no filesystem
access to the indexer host required.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from felix.api.search import SearchClient
from felix.config import Config


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return fallback


def catalog_chunks(catalog_dir: Path, source_name: str) -> list[dict[str, Any]]:
    """One document-section chunk per catalog .md, in upload format."""
    chunks: list[dict[str, Any]] = []
    for md in sorted(catalog_dir.glob("*.md")):
        text = md.read_text(encoding="utf-8").strip()
        if not text:
            continue
        doc = md.stem
        chunks.append(
            {
                "chunk_id": f"{source_name}:{doc}",
                "text": text,
                "content_hash": _sha256(text),
                "payload": {
                    "source_type": "document",
                    "source_name": source_name,
                    "document_name": doc,
                    "chunk_type": "document_section",
                    "section_title": _title(text, doc),
                },
            }
        )
    return chunks


@dataclass
class IndexResult:
    chunks: int
    indexer_response: dict


def build_and_index(config: Config, *, version: str | None = None, clean: bool = False) -> IndexResult:
    """Upload the catalog chunks over HTTP and index them into Qdrant.

    Incremental by default (clean=False): unchanged chunks are skipped, only new
    skills are embedded — safe for the auto-discovery path. clean=True does a
    full wipe+rebuild (explicit `felix skills index --clean` only)."""
    chunks = catalog_chunks(config.catalog_dir, config.skills_source_name)
    with SearchClient(config) as client:
        resp = client.upload_source(config.skills_source_name, chunks, version=version, clean=clean)
    return IndexResult(chunks=len(chunks), indexer_response=resp)
