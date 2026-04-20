from __future__ import annotations as _annotations

import asyncio
from typing import Any

import pytest

from examples.acpremote import (
    connect_codex,
    connect_mirror,
    expose_codex,
    serve_langchain_workspace,
    serve_pydantic_finance,
)


class _FakeServer:
    def __init__(self) -> None:
        self.closed = False
        self.waited = False
        self.served = False

    async def serve_forever(self) -> None:
        self.served = True

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        self.waited = True


def test_pydantic_remote_example_builds_acp_agent_and_serves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    server = _FakeServer()

    def fake_create_acp_agent(*, agent: Any, config: Any) -> object:
        captured["agent"] = agent
        captured["config"] = config
        return object()

    async def fake_serve_acp(acp_agent: Any, **kwargs: Any) -> _FakeServer:
        captured["acp_agent"] = acp_agent
        captured["kwargs"] = kwargs
        return server

    monkeypatch.setenv("ACPREMOTE_HOST", "0.0.0.0")
    monkeypatch.setenv("ACPREMOTE_PORT", "9001")
    monkeypatch.setenv("ACPREMOTE_MOUNT_PATH", "/finance")
    monkeypatch.setenv("ACPREMOTE_BEARER_TOKEN", "secret-token")
    monkeypatch.setattr(serve_pydantic_finance, "create_acp_agent", fake_create_acp_agent)
    monkeypatch.setattr(serve_pydantic_finance, "serve_acp", fake_serve_acp)

    asyncio.run(serve_pydantic_finance.main())

    assert captured["agent"] is serve_pydantic_finance.agent
    assert captured["config"] is serve_pydantic_finance.config
    assert captured["kwargs"] == {
        "host": "0.0.0.0",
        "port": 9001,
        "mount_path": "/finance",
        "bearer_token": "secret-token",
    }
    assert server.served is True
    assert server.closed is True
    assert server.waited is True


def test_langchain_remote_example_builds_acp_agent_and_serves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    server = _FakeServer()

    def fake_create_acp_agent(*, graph_factory: Any, config: Any) -> object:
        captured["graph_factory"] = graph_factory
        captured["config"] = config
        return object()

    async def fake_serve_acp(acp_agent: Any, **kwargs: Any) -> _FakeServer:
        captured["acp_agent"] = acp_agent
        captured["kwargs"] = kwargs
        return server

    monkeypatch.setenv("ACPREMOTE_PORT", "9002")
    monkeypatch.setattr(serve_langchain_workspace, "create_acp_agent", fake_create_acp_agent)
    monkeypatch.setattr(serve_langchain_workspace, "serve_acp", fake_serve_acp)

    asyncio.run(serve_langchain_workspace.main())

    assert captured["graph_factory"] is serve_langchain_workspace.graph_from_session
    assert captured["config"] is serve_langchain_workspace.config
    assert captured["kwargs"] == {
        "host": "127.0.0.1",
        "port": 9002,
        "mount_path": "/acp",
        "bearer_token": None,
    }
    assert server.served is True
    assert server.closed is True
    assert server.waited is True


def test_connect_mirror_uses_transport_options_and_root_connect_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_connect_acp(url: str, **kwargs: Any) -> object:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return object()

    async def fake_run_agent(agent: Any) -> None:
        captured["run_agent"] = agent

    monkeypatch.setenv("ACPREMOTE_URL", "ws://example.com/acp/ws")
    monkeypatch.setenv("ACPREMOTE_BEARER_TOKEN", "mirror-token")
    monkeypatch.setenv("ACPREMOTE_EMIT_LATENCY_META", "false")
    monkeypatch.setenv("ACPREMOTE_EMIT_LATENCY_PROJECTION", "true")
    monkeypatch.setattr(connect_mirror, "connect_acp", fake_connect_acp)
    monkeypatch.setattr(connect_mirror, "run_agent", fake_run_agent)

    asyncio.run(connect_mirror.main())

    options = captured["kwargs"]["options"]
    assert captured["url"] == "ws://example.com/acp/ws"
    assert captured["kwargs"]["bearer_token"] == "mirror-token"
    assert options.emit_latency_meta is False
    assert options.emit_latency_projection is True
    assert captured["run_agent"] is not None


def test_codex_examples_cover_env_parsing_and_transport_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _FakeServer()
    captured: dict[str, Any] = {}

    async def fake_serve_command(command: Any, **kwargs: Any) -> _FakeServer:
        captured["command"] = command
        captured["serve_kwargs"] = kwargs
        return server

    def fake_connect_acp(url: str, **kwargs: Any) -> object:
        captured["connect_url"] = url
        captured["connect_kwargs"] = kwargs
        return object()

    async def fake_run_agent(agent: Any) -> None:
        captured["run_agent"] = agent

    monkeypatch.setenv("ACPREMOTE_COMMAND", "npx @zed-industries/codex-acp --profile demo")
    monkeypatch.setenv("GOOGLE_API_KEY", "demo-key")
    monkeypatch.setattr(expose_codex, "serve_command", fake_serve_command)
    asyncio.run(expose_codex.main())

    monkeypatch.setenv("ACPREMOTE_URL", "ws://127.0.0.1:8080/acp/ws")
    monkeypatch.setenv("ACPREMOTE_BEARER_TOKEN", "codex-token")
    monkeypatch.setattr(connect_codex, "connect_acp", fake_connect_acp)
    monkeypatch.setattr(connect_codex, "run_agent", fake_run_agent)
    asyncio.run(connect_codex.main())

    assert captured["command"] == (
        "npx",
        "@zed-industries/codex-acp",
        "--profile",
        "demo",
    )
    assert captured["serve_kwargs"]["env"] == {"GOOGLE_API_KEY": "demo-key"}
    assert captured["connect_url"] == "ws://127.0.0.1:8080/acp/ws"
    assert captured["connect_kwargs"]["bearer_token"] == "codex-token"
    options = captured["connect_kwargs"]["options"]
    assert options.emit_latency_meta is True
    assert options.emit_latency_projection is True
