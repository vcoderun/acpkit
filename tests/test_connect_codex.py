from __future__ import annotations as _annotations

import asyncio
import runpy
from typing import Any

import pytest

import connect_codex


def test_connect_codex_helpers_resolve_env_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ACPREMOTE_URL", raising=False)
    monkeypatch.delenv("ACPREMOTE_BEARER_TOKEN", raising=False)

    assert connect_codex._remote_url() == "ws://127.0.0.1:8080/acp/ws"
    assert connect_codex._bearer_token() is None

    monkeypatch.setenv("ACPREMOTE_URL", "ws://remote.example/acp/ws")
    monkeypatch.setenv("ACPREMOTE_BEARER_TOKEN", "  secret-token  ")

    assert connect_codex._remote_url() == "ws://remote.example/acp/ws"
    assert connect_codex._bearer_token() == "secret-token"


@pytest.mark.asyncio
async def test_connect_codex_main_connects_remote_agent_with_latency_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    agent = object()

    def fake_connect_acp(
        url: str,
        *,
        bearer_token: str | None,
        options: Any,
    ) -> object:
        captured["url"] = url
        captured["bearer_token"] = bearer_token
        captured["options"] = options
        return agent

    async def fake_run_agent(received_agent: object) -> None:
        captured["agent"] = received_agent

    monkeypatch.setenv("ACPREMOTE_URL", "ws://remote.example/acp/ws")
    monkeypatch.setenv("ACPREMOTE_BEARER_TOKEN", "demo-token")
    monkeypatch.setattr(connect_codex, "connect_acp", fake_connect_acp)
    monkeypatch.setattr(connect_codex, "run_agent", fake_run_agent)

    await connect_codex.main()

    assert captured["url"] == "ws://remote.example/acp/ws"
    assert captured["bearer_token"] == "demo-token"
    assert captured["agent"] is agent
    assert captured["options"].emit_latency_meta is True
    assert captured["options"].emit_latency_projection is True


def test_connect_codex_module_main_executes_asyncio_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []

    def fake_run(coro: Any) -> None:
        calls.append(coro)
        coro.close()

    monkeypatch.setattr(asyncio, "run", fake_run)
    runpy.run_module("connect_codex", run_name="__main__")

    assert len(calls) == 1
