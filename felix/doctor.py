"""`felix doctor` — self-check the Felix <-> agent-forge integration.

Confirms the API is reachable, the WebSocket opens, the SAQ worker is present,
the @felix agent exists, and the run-store is writable.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from felix.api.rest import RestClient
from felix.api.search import SearchClient
from felix.api.ws import WSClient
from felix.config import Config, felix_home


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def _check_home() -> Check:
    home = felix_home()
    try:
        home.mkdir(parents=True, exist_ok=True)
        probe = home / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        return Check("run-store writable", True, str(home))
    except Exception as exc:  # noqa: BLE001
        return Check("run-store writable", False, str(exc))


def _check_rest(config: Config) -> list[Check]:
    checks: list[Check] = []
    rest = RestClient(config)
    try:
        # Connectivity first — bail if the API can't even be reached.
        try:
            health = rest.health()
            checks.append(Check("api health", str(health.get("status")) == "ok", str(health)))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("api reachable", False, f"{config.api_base}: {exc}"))
            return checks

        # @felix agent present?
        try:
            agents = rest.agent_aliases()
            target = config.default_agent.lstrip("@")
            checks.append(
                Check(
                    f"agent {config.default_agent} available",
                    target in agents,
                    "found" if target in agents else f"missing — known: {sorted(agents)[:8]}",
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(Check(f"agent {config.default_agent} available", False, f"/api/agents error: {exc}"))

        # SAQ worker present in /api/services?
        try:
            worker = _find_worker(rest.services())
            checks.append(Check("SAQ worker present", worker is not None, worker or "no worker service found"))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("SAQ worker present", False, f"/api/services error: {exc}"))
    finally:
        rest.close()
    return checks


def _find_worker(services: object) -> str | None:
    """Locate a worker/Ally entry in the /api/services payload."""
    items: list[dict] = []
    if isinstance(services, dict):
        maybe = services.get("services", services)
        items = maybe if isinstance(maybe, list) else list(maybe.values()) if isinstance(maybe, dict) else []
    elif isinstance(services, list):
        items = services
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).lower()
        if "worker" in name or "ally" in name or "saq" in name or "huey" in name:
            return item.get("name")
    return None


async def _check_ws(config: Config) -> Check:
    ws = WSClient(config)
    try:
        await asyncio.wait_for(ws.connect(), timeout=10)
        await ws.close()
        return Check("websocket connect", True, config.ws_url)
    except Exception as exc:  # noqa: BLE001
        return Check("websocket connect", False, f"{config.ws_url}: {exc}")


def _check_search(config: Config) -> Check:
    """The indexer/search service backing the skill fleet."""
    try:
        with SearchClient(config, timeout=15) as client:
            info = client.collection()
        return Check("search service (skill fleet)", True, str(info)[:120])
    except Exception as exc:  # noqa: BLE001
        return Check("search service (skill fleet)", False, f"{config.search_base}: {exc}")


def run_doctor(config: Config) -> list[Check]:
    checks = [_check_home(), *_check_rest(config)]
    checks.append(_check_search(config))
    checks.append(asyncio.run(_check_ws(config)))
    return checks
