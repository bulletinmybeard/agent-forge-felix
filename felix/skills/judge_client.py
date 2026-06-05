"""Direct provider transport for the skill firewall's LLM judge.

Felix calls the judge itself (not via agent-forge) so vetting stays independent
of the server. Speaks the OpenAI-style /chat/completions shape, which covers
openrouter / deepinfra / ollama. Kept separate from vet.py so the judge logic
stays unit-testable with a fake client.
"""

from __future__ import annotations

import httpx

from felix.config import Config

# Providers that don't need a base_url in config — sensible local/default endpoint.
_DEFAULT_BASE_URL = {"ollama": "http://localhost:11434/v1"}


class JudgeClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(headers=headers, timeout=timeout, transport=transport)

    def complete(self, system: str, user: str) -> str:
        resp = self._client.post(
            self._url,
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def close(self) -> None:
        self._client.close()


def build_judge_client(config: Config) -> JudgeClient | None:
    """Construct a judge client from config, or None if vetting has no provider
    (the static-only path)."""
    if not config.vetting_provider:
        return None
    base_url = config.vetting_base_url or _DEFAULT_BASE_URL.get(config.vetting_provider, "")
    if not base_url:
        return None
    return JudgeClient(
        base_url=base_url,
        model=config.vetting_model,
        api_key=config.vetting_api_key,
    )
