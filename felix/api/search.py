"""Client for agent-forge's indexer/search service (the :8100 API).

Hosts the skill fleet retrieval (`/search`, `/search/smart`)
and the indexing trigger (`/indexer/index/{name}`).
Separate base URL from the chat service.
"""

from __future__ import annotations

from typing import Any

import httpx

from felix.config import Config


class SearchClient:
    def __init__(self, config: Config, *, timeout: float = 60.0) -> None:
        self._config = config
        self._client = httpx.Client(
            base_url=config.search_base,
            headers=config.auth_headers(),
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SearchClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- search ----------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        smart: bool = True,
        limit: int = 5,
        score_threshold: float | None = None,
        source_name: str | None = None,
        chunk_type: str | None = "document_section",
        timeout: float | None = None,
    ) -> list[dict[str, Any]]:
        """Return the raw `results` list from /search or /search/smart.

        timeout overrides the client default for this call — used to cap the
        smart (cloud query-refinement) hop so a slow refiner falls back to raw
        search fast instead of stalling the whole run."""
        body: dict[str, Any] = {"query": query, "limit": limit}
        if score_threshold is not None:
            body["score_threshold"] = score_threshold
        if source_name:
            body["source_name"] = source_name
        if chunk_type:
            body["chunk_type"] = chunk_type
        path = "/search/smart" if smart else "/search"
        post_kwargs = {} if timeout is None else {"timeout": timeout}
        resp = self._client.post(path, json=body, **post_kwargs)
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", []) if isinstance(data, dict) else []

    # -- indexing --------------------------------------------------------
    def index_source(
        self,
        source_name: str,
        *,
        source_type: str = "document",
        version: str | None = None,
        clean: bool = False,
    ) -> dict[str, Any]:
        """Trigger (re)indexing of an already-on-disk source into Qdrant.

        clean=False is incremental (content_hash skip); clean=True wipes first."""
        params: dict[str, Any] = {"source_type": source_type, "clean": clean}
        if version:
            params["version"] = version
        resp = self._client.post(f"/indexer/index/{source_name}", params=params)
        resp.raise_for_status()
        return resp.json()

    def upload_source(
        self,
        source_name: str,
        chunks: list[dict[str, Any]],
        *,
        source_type: str = "document",
        version: str | None = None,
        clean: bool = False,
    ) -> dict[str, Any]:
        """Upload chunks over HTTP and index them — no filesystem access to the
        indexer host needed. Requires agent-forge's POST /indexer/upload.

        clean defaults to False: the indexer skips unchanged chunks by
        content_hash and only embeds new ones, so re-indexing is safe and
        incremental. clean=True wipes the source's points first — only for an
        explicit full rebuild (a mid-rebuild failure would leave 0 points)."""
        body: dict[str, Any] = {"source_type": source_type, "clean": clean, "chunks": chunks}
        if version:
            body["version"] = version
        resp = self._client.post(f"/indexer/upload/{source_name}", json=body, timeout=300.0)
        resp.raise_for_status()
        return resp.json()

    def sources(self) -> Any:
        resp = self._client.get("/indexer/sources")
        resp.raise_for_status()
        return resp.json()

    def collection(self) -> Any:
        resp = self._client.get("/indexer/collection")
        resp.raise_for_status()
        return resp.json()
