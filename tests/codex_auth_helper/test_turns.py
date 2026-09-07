from __future__ import annotations as _annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from codex_auth_helper import CodexAuthConfig, create_codex_async_openai, create_codex_openai
from langchain_core.messages import HumanMessage
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from websockets.datastructures import Headers

from .support import write_auth_file
from .test_langchain import _model as _langchain_model
from .test_websocket import _client, _FakeResponses, _model, _text_events

KEY = "x-codex-turn-state"


def test_ambiguous_duplicate_turn_header_is_ignored() -> None:
    from codex_auth_helper._responses_turn import ResponsesTurnState

    state = ResponsesTurnState()
    state.capture(Headers([(KEY, "one"), (KEY, "two")]))
    assert state.value is None
    state.capture({KEY: "unambiguous"})
    assert state.value == "unambiguous"


async def test_handshake_state_ignores_unrelated_duplicate_headers_and_survives_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = _FakeResponses(event_batches=[_text_events("r1", "ok"), _text_events("r2", "ok")])
    original_connect = resource.connect

    def connect(**kwargs: Any) -> Any:
        manager = original_connect(**kwargs)
        manager.connection._connection.response = SimpleNamespace(
            headers=Headers(
                [
                    ("set-cookie", "synthetic-one"),
                    ("set-cookie", "synthetic-two"),
                    (KEY, "handshake-route"),
                ]
            )
        )
        return manager

    monkeypatch.setattr(resource, "connect", connect)
    client, http = _client(resource)
    try:
        async with client.responses_session(connection="websocket"), client.responses_turn():
            for i in range(2):
                stream = await client.responses.create(model="gpt-test", input="x", stream=True)
                async with stream:
                    async for _ in stream:
                        pass
                resource.connections[i]._connection.close_code = 1000
        assert KEY not in resource.connect_kwargs[0]["extra_headers"]
        assert resource.connect_kwargs[1]["extra_headers"][KEY] == "handshake-route"
        assert all(
            c.sent[0]["client_metadata"][KEY] == "handshake-route" for c in resource.connections
        )
    finally:
        await http.aclose()


async def test_http_turn_first_value_wins_nested_reset_and_exception(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    write_auth_file(auth)
    seen: list[str | None] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.headers.get(KEY))
        return httpx2.Response(200, json={}, headers={KEY: f"route-{len(seen)}"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        client = create_codex_async_openai(config=CodexAuthConfig(auth_path=auth), http_client=http)
        try:
            async with client.responses_turn():
                await client.responses.create(model="gpt-test", input="one")
                await client.responses.create(model="gpt-test", input="two")
                async with client.responses_turn():
                    await client.responses.create(model="gpt-test", input="nested")
                await client.responses.create(model="gpt-test", input="outer")
            with pytest.raises(RuntimeError, match="stop"):
                async with client.responses_turn():
                    await client.responses.create(model="gpt-test", input="new")
                    raise RuntimeError("stop")
            await client.responses.create(model="gpt-test", input="unscoped")
        finally:
            await client.close()
    assert seen == [None, "route-1", None, "route-1", None, None]


def test_sync_http_turn_state_and_caller_mapping(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    write_auth_file(auth)
    seen: list[str | None] = []
    supplied = {"X-Codex-Turn-State": "stale", "x-synthetic": "keep"}

    def respond(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.headers.get(KEY))
        assert request.headers["x-synthetic"] == "keep"
        return httpx2.Response(200, json={}, headers={KEY: "fresh"})

    with httpx2.Client(transport=httpx2.MockTransport(respond)) as http:
        client = create_codex_openai(config=CodexAuthConfig(auth_path=auth), http_client=http)
        try:
            for _ in range(2):
                with client.responses_turn():
                    for _ in range(2):
                        client.responses.create(model="gpt-test", input="x", extra_headers=supplied)
        finally:
            client.close()
    assert seen == [None, "fresh", None, "fresh"]
    assert supplied == {"X-Codex-Turn-State": "stale", "x-synthetic": "keep"}


@pytest.mark.parametrize("framework", ["pydantic", "langchain"])
async def test_native_turns_reuse_socket_without_leaking_routing_or_resetting_cursor(
    framework: str,
) -> None:
    batches = [
        [
            SimpleNamespace(type="codex.response.metadata", headers={KEY: f"route-{i}"}),
            *_text_events(f"resp_{i}", "ok"),
        ]
        for i in range(3)
    ]
    resource = _FakeResponses(event_batches=batches)
    client, http = _client(resource)
    model: Any = _model(client) if framework == "pydantic" else _langchain_model(client)
    py_history: list[ModelRequest | ModelResponse] = []
    lc_history: list[Any] = []

    async def invoke(label: str) -> None:
        if framework == "pydantic":
            py_history.append(ModelRequest(parts=[UserPromptPart(label)]))
            answer = await model.request(py_history, None, ModelRequestParameters())
            py_history.append(answer)
        else:
            lc_history.append(HumanMessage(label))
            answer = await model.ainvoke(lc_history)
            lc_history.append(answer)

    try:
        async with model.responses_session():
            async with model.responses_turn():
                await invoke("one")
                await invoke("two")
            async with model.responses_turn():
                await invoke("three")
        assert len(resource.connections) == 1
        first, second, third = resource.connections[0].sent
        assert KEY not in first.get("client_metadata", {})
        assert second["client_metadata"][KEY] == "route-0"
        assert KEY not in third.get("client_metadata", {})
        assert second["previous_response_id"] == "resp_0"
        assert third["previous_response_id"] == "resp_1"
        assert len(second["input"]) == len(third["input"]) == 1
        assert resource.connections[0].closed
    finally:
        await http.aclose()


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        {"unrelated": "value"},
        {KEY: None},
        {KEY: 42},
        {KEY: ""},
        {KEY: "a\nb"},
        {KEY: "x" * 8193},
    ],
)
async def test_websocket_ignores_invalid_state_and_preserves_client_metadata(bad: object) -> None:
    resource = _FakeResponses(
        event_batches=[
            [
                SimpleNamespace(type="codex.response.metadata", headers=bad),
                *_text_events("resp_0", "ok"),
            ],
            _text_events("resp_1", "ok"),
        ]
    )
    client, http = _client(resource)
    metadata = {"application": "synthetic"}
    try:
        async with client.responses_session(connection="websocket"), client.responses_turn():
            for _ in range(2):
                stream = await client.responses.create(
                    model="gpt-test",
                    input="x",
                    stream=True,
                    extra_body={"client_metadata": metadata},
                )
                async with stream:
                    async for _ in stream:
                        pass
        assert all(event["client_metadata"] == metadata for event in resource.connections[0].sent)
        assert metadata == {"application": "synthetic"}
    finally:
        await http.aclose()


async def test_concurrent_http_turns_are_isolated_and_cancelled_turn_does_not_leak(
    tmp_path: Path,
) -> None:
    auth = tmp_path / "auth.json"
    write_auth_file(auth)
    seen: list[str | None] = []
    ready = asyncio.Event()
    release = asyncio.Event()

    def respond(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.headers.get(KEY))
        return httpx2.Response(200, json={}, headers={KEY: f"route-{len(seen)}"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        client = create_codex_async_openai(config=CodexAuthConfig(auth_path=auth), http_client=http)

        async def interrupted() -> None:
            async with client.responses_turn():
                await client.responses.create(model="gpt-test", input="first")
                ready.set()
                await release.wait()

        task = asyncio.create_task(interrupted())
        try:
            await asyncio.wait_for(ready.wait(), 2)
            async with client.responses_turn():
                await client.responses.create(model="gpt-test", input="independent")
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await client.responses.create(model="gpt-test", input="same")
            await client.responses.create(model="gpt-test", input="unscoped")
        finally:
            task.cancel()
            await client.close()
    assert seen == [None, None, "route-2", None]


async def test_closed_turn_does_not_leak_to_late_child_request(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    write_auth_file(auth)
    seen: list[str | None] = []
    release = asyncio.Event()

    def respond(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.headers.get(KEY))
        return httpx2.Response(200, json={}, headers={KEY: "synthetic-route"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        client = create_codex_async_openai(config=CodexAuthConfig(auth_path=auth), http_client=http)

        async def late() -> None:
            await release.wait()
            await client.responses.create(model="gpt-test", input="late")

        try:
            async with client.responses_turn():
                await client.responses.create(model="gpt-test", input="initial")
                child = asyncio.create_task(late())
            release.set()
            await asyncio.wait_for(child, 2)
        finally:
            await client.close()
    assert seen == [None, None]


@pytest.mark.parametrize("early_close", [False, True])
async def test_turn_state_handles_socket_reconnect_and_early_close(early_close: bool) -> None:
    resource = _FakeResponses(
        event_batches=[
            [
                SimpleNamespace(type="codex.response.metadata", headers={KEY: "route-first"}),
                *_text_events("r1", "ok"),
            ],
            _text_events("r2", "ok"),
        ]
    )
    client, http = _client(resource)

    async def invoke(stop: bool = False) -> None:
        stream = await client.responses.create(model="gpt-test", input="x", stream=True)
        async with stream:
            async for _ in stream:
                if stop:
                    break

    try:
        async with client.responses_session(connection="websocket"):
            async with client.responses_turn():
                await invoke(early_close)
                if not early_close:
                    await resource.connections[0].close()
                    await invoke()
            if early_close:
                async with client.responses_turn():
                    await invoke()
        assert len(resource.connections) == 2
        sent = resource.connections[1].sent[0]
        assert sent.get("client_metadata", {}).get(KEY) == (None if early_close else "route-first")
        assert "previous_response_id" not in sent
    finally:
        await http.aclose()
