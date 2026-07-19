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

    def command_permissions(self) -> dict[str, Any]:
        """YAML + override + effective shell/SSH policy (AgentForge ≥ 0.12)."""
        return self._get("/api/permissions/commands")

    def get_command_overrides(self) -> dict[str, Any]:
        """Runtime overrides only (null when unset)."""
        return self._get("/api/permissions/commands/overrides")

    def put_command_overrides(self, body: dict[str, Any]) -> dict[str, Any]:
        """Upsert shell and/or ssh runtime overrides (full document per tool)."""
        resp = self._client.put("/api/permissions/commands/overrides", json=body)
        resp.raise_for_status()
        return resp.json() if resp.content else {"ok": True}

    def delete_command_overrides(self, tool: str | None = None) -> dict[str, Any]:
        """Delete one override (tool=shell|ssh) or all when tool is omitted."""
        params = {"tool": tool} if tool else None
        resp = self._client.delete("/api/permissions/commands/overrides", params=params)
        resp.raise_for_status()
        return resp.json() if resp.content else {"deleted": 0}

    def validate_command(
        self,
        tool: str,
        command: str,
        *,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Evaluate a command against effective policy (optional draft override)."""
        body: dict[str, Any] = {"tool": tool, "command": command}
        if policy is not None:
            body["policy"] = policy
        resp = self._client.post("/api/permissions/commands/validate", json=body)
        resp.raise_for_status()
        return resp.json()

    def list_permission_profiles(self) -> dict[str, Any]:
        return self._get("/api/permissions/profiles")

    def get_permission_profile(self, profile_id: str) -> dict[str, Any]:
        return self._get(f"/api/permissions/profiles/{profile_id}")

    def apply_permission_profile(self, profile_id: str) -> dict[str, Any]:
        resp = self._client.post(f"/api/permissions/profiles/{profile_id}/apply")
        resp.raise_for_status()
        return resp.json() if resp.content else {"ok": True}

    def save_permission_profile(self, profile_id: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.put(f"/api/permissions/profiles/{profile_id}", json=body)
        resp.raise_for_status()
        return resp.json() if resp.content else {"ok": True}

    def delete_permission_profile(self, profile_id: str) -> dict[str, Any]:
        resp = self._client.delete(f"/api/permissions/profiles/{profile_id}")
        resp.raise_for_status()
        return resp.json() if resp.content else {"deleted": 0}
