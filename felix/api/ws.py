"""WebSocket driver for agent-forge's /ws/chat.

Transport only: connect, send JSON messages, and yield decoded events.
All policy (confirm decisions, persistence, rendering) lives in the orchestrator.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import websockets

from felix.api.events import Event, parse_event
from felix.config import Config


class WSClient:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._conn: Any = None

    async def connect(self, session_id: str | None = None) -> None:
        uri = self._config.ws_url
        if session_id:
            sep = "&" if "?" in uri else "?"
            uri = f"{uri}{sep}session_id={session_id}"

        headers = self._config.auth_headers()
        subprotocols = [self._config.api_key] if self._config.api_key else None

        # websockets renamed extra_headers -> additional_headers in v14.
        try:
            self._conn = await websockets.connect(uri, additional_headers=headers or None, subprotocols=subprotocols)
        except TypeError:
            self._conn = await websockets.connect(uri, extra_headers=headers or None, subprotocols=subprotocols)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def send(self, message: dict[str, Any]) -> None:
        if self._conn is None:
            raise RuntimeError("WSClient.send called before connect()")
        await self._conn.send(json.dumps(message))

    async def events(self) -> AsyncIterator[Event]:
        """Yield decoded events until the connection closes."""
        if self._conn is None:
            raise RuntimeError("WSClient.events called before connect()")
        async for raw in self._conn:
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(payload, dict):
                yield parse_event(payload)

    async def __aenter__(self) -> WSClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
