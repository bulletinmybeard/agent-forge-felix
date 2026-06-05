"""Skill firewall — Gate 2 transport (direct provider call).

JudgeClient is the only networked piece. Tested with httpx.MockTransport
so no real provider is hit; build_judge_client is the config > client factory.
"""

from __future__ import annotations

import json

import httpx

from felix.config import Config
from felix.skills.judge_client import JudgeClient, build_judge_client


def test_build_returns_none_without_provider():
    assert build_judge_client(Config()) is None


def test_build_returns_client_when_provider_set():
    cfg = Config(
        vetting_provider="openrouter",
        vetting_model="x/y",
        vetting_base_url="https://api.test/v1",
        vetting_api_key="k",
    )
    assert isinstance(build_judge_client(cfg), JudgeClient)


def test_complete_posts_chat_messages_and_returns_content():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "VERDICT-JSON"}}]})

    client = JudgeClient(
        base_url="https://api.test/v1",
        model="x/y",
        api_key="k",
        transport=httpx.MockTransport(handler),
    )
    out = client.complete("SYS", "USR")

    assert out == "VERDICT-JSON"
    assert captured["url"] == "https://api.test/v1/chat/completions"
    assert captured["body"]["model"] == "x/y"
    assert [m["role"] for m in captured["body"]["messages"]] == ["system", "user"]
    assert captured["body"]["messages"][1]["content"] == "USR"
    assert captured["auth"] == "Bearer k"
