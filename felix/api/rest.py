"""REST helpers for the agent-forge web API.

Only the read-only endpoints Felix needs: health, agents, skills, profiles,
dry-run, sessions, and services (to locate the SAQ worker).
"""

from __future__ import annotations

from typing import Any

import httpx

from felix.config import Config


class RestClient:
    def __init__(self, config: Config, *, timeout: float = 30.0) -> None:
        self._config = config
        self._client = httpx.Client(
            base_url=config.api_base,
            headers=config.auth_headers(),
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> RestClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get(self, path: str, **params: Any) -> Any:
        resp = self._client.get(path, params={k: v for k, v in params.items() if v is not None})
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _unwrap(data: Any, key: str) -> list[dict[str, Any]]:
        """List endpoints return either a bare list or a {key: [...]} envelope."""
        if isinstance(data, dict):
            inner = data.get(key, [])
            return inner if isinstance(inner, list) else []
        return data if isinstance(data, list) else []

    # -- endpoints -------------------------------------------------------
    def health(self) -> dict[str, Any]:
        return self._get("/api/health")

    def agents(self) -> list[dict[str, Any]]:
        return self._unwrap(self._get("/api/agents"), "agents")

    def skills(self) -> list[dict[str, Any]]:
        return self._unwrap(self._get("/api/skills"), "skills")

    def profiles(self, *, include_abstract: bool = False) -> list[dict[str, Any]]:
        return self._unwrap(self._get("/api/profiles", include_abstract=include_abstract), "profiles")

    def services(self) -> Any:
        return self._get("/api/services")

    def dry_run(self, query: str, *, session_id: str | None = None, last_mode: str = "chat") -> dict[str, Any]:
        body: dict[str, Any] = {"query": query, "last_mode": last_mode}
        if session_id:
            body["session_id"] = session_id
        resp = self._client.post("/api/dry-run", json=body)
        resp.raise_for_status()
        return resp.json()

    def session_messages(self, session_id: str, *, limit: int = 0) -> dict[str, Any]:
        return self._get(f"/api/sessions/{session_id}/messages", limit=limit)

    def agent_aliases(self) -> set[str]:
        """All @-aliases the server currently exposes (modes + custom agents)."""
        aliases: set[str] = set()
        for agent in self.agents():
            if not isinstance(agent, dict):
                continue
            for alias in agent.get("aliases", []) or []:
                aliases.add(str(alias).lstrip("@"))
        return aliases
