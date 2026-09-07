from __future__ import annotations as _annotations

import json
from pathlib import Path
from typing import Any, Literal

import httpx
import httpx2
import pytest
from codex_auth_helper import (
    CodexAuthConfig,
    CodexResponsesModel,
    __version__,
    create_codex_async_openai,
    create_codex_openai,
)
from langchain_core.messages import HumanMessage
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.providers.openai import OpenAIProvider

from .support import write_auth_file
from .test_langchain import _model as _langchain_model
from .test_websocket import _client, _FakeResponses, _model, _text_events


@pytest.mark.parametrize("tier", [None, "default", "flex", "priority"])
async def test_http_headers_use_own_identity_and_effective_routing(
    tmp_path: Path, tier: Literal["default", "flex", "priority"] | None
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path)
    seen: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={}, request=request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        client = create_codex_async_openai(
            config=CodexAuthConfig(auth_path=auth_path), http_client=http
        )
        try:
            await client.responses.create(model="gpt-test", input="hello", service_tier=tier)
        finally:
            await client.close()
    headers = seen[0].headers
    assert headers["originator"] == "codex-auth-helper"
    assert headers["user-agent"] == f"codex-auth-helper/{__version__}"
    assert headers["x-codex-routing-hint"] == "model=gpt-test" + (
        f";tier={tier}" if tier is not None else ""
    )
    assert "openai-beta" not in headers
    assert json.loads(seen[0].content)["model"] == "gpt-test"


def test_sync_http_routing_uses_extra_body_and_preserves_explicit_headers(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path)
    seen: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={}, request=request)

    supplied = {"user-agent": "my-client/2", "ORIGINATOR": "my-client"}
    with httpx2.Client(transport=httpx2.MockTransport(respond)) as http:
        client = create_codex_openai(config=CodexAuthConfig(auth_path=auth_path), http_client=http)
        try:
            client.responses.create(
                model="gpt-test",
                input="hello",
                extra_body={"model": "gpt-other", "service_tier": "priority"},
                extra_headers=supplied,
            )
        finally:
            client.close()
    assert seen[0].headers["x-codex-routing-hint"] == "model=gpt-other;tier=priority"
    assert seen[0].headers["user-agent"] == "my-client/2"
    assert seen[0].headers["originator"] == "my-client"
    assert supplied == {"user-agent": "my-client/2", "ORIGINATOR": "my-client"}
    assert "openai-beta" not in seen[0].headers


@pytest.mark.parametrize("framework", ["pydantic", "langchain"])
@pytest.mark.parametrize("explicit", [False, True])
async def test_native_websocket_identity_headers_and_unchanged_continuation(
    framework: str, explicit: bool
) -> None:
    resource = _FakeResponses(event_batches=[_text_events(f"resp_{i}", "ok") for i in range(2)])
    client, http = _client(resource)
    supplied = (
        {"user-agent": "my-client/2", "ORIGINATOR": "my-client", "X-Codex-Routing-Hint": "route"}
        if explicit
        else {"x-synthetic": "retained"}
    )
    original = dict(supplied)
    try:
        if framework == "pydantic":
            model = _model(client)
            settings: Any = {"extra_headers": supplied}
            async with model.responses_session():
                messages: list[ModelRequest | ModelResponse] = [
                    ModelRequest(parts=[UserPromptPart("one")])
                ]
                first = await model.request(messages, settings, ModelRequestParameters())
                await model.request(
                    [*messages, first, ModelRequest(parts=[UserPromptPart("two")])],
                    settings,
                    ModelRequestParameters(),
                )
            model_name = "gpt-test"
        else:
            model = _langchain_model(client)
            async with model.responses_session():
                history: list[Any] = [HumanMessage("one")]
                first = await model.ainvoke(history, extra_headers=supplied)
                await model.ainvoke([*history, first, HumanMessage("two")], extra_headers=supplied)
            model_name = "gpt-5.6-luna"
        assert supplied == original
        assert len(resource.connections) == 1
        assert resource.connections[0].sent[1]["previous_response_id"] == "resp_0"
        headers = httpx.Headers(resource.connect_kwargs[0]["extra_headers"])
        assert headers["user-agent"] == (
            "my-client/2" if explicit else f"codex-auth-helper/{__version__}"
        )
        assert headers["originator"] == ("my-client" if explicit else "codex-auth-helper")
        assert headers["x-codex-routing-hint"] == ("route" if explicit else f"model={model_name}")
        assert headers["openai-beta"] == "responses_websockets=2026-02-06"
        assert all("extra_headers" not in event for event in resource.connections[0].sent)
    finally:
        await http.aclose()


@pytest.mark.parametrize("framework", ["pydantic", "langchain"])
@pytest.mark.parametrize("change", ["model", "priority"])
async def test_routing_change_reconnects_and_sends_full_history(
    framework: str, change: str
) -> None:
    resource = _FakeResponses(event_batches=[_text_events(f"resp_{i}", "ok") for i in range(2)])
    client, http = _client(resource)
    try:
        if framework == "pydantic":
            model = _model(client)
            async with model.responses_session():
                messages: list[ModelRequest | ModelResponse] = [
                    ModelRequest(parts=[UserPromptPart("one")])
                ]
                first = await model.request(messages, None, ModelRequestParameters())
                next_model = (
                    CodexResponsesModel(
                        "gpt-other",
                        default_instructions="Keep the response precise.",
                        provider=OpenAIProvider(openai_client=client),
                        connection="websocket",
                    )
                    if change == "model"
                    else model
                )
                settings: Any = (
                    {"openai_service_tier": "priority"} if change == "priority" else None
                )
                await next_model.request(
                    [*messages, first, ModelRequest(parts=[UserPromptPart("two")])],
                    settings,
                    ModelRequestParameters(),
                )
            original_name = "gpt-test"
        else:
            model = _langchain_model(client)
            async with model.responses_session():
                history: list[Any] = [HumanMessage("one")]
                first = await model.ainvoke(history)
                kwargs: dict[str, Any] = (
                    {"model": "gpt-other"} if change == "model" else {"service_tier": "priority"}
                )
                await model.ainvoke([*history, first, HumanMessage("two")], **kwargs)
            original_name = "gpt-5.6-luna"
        assert len(resource.connections) == 2
        assert resource.connections[0].closed
        second = resource.connections[1].sent[0]
        assert "previous_response_id" not in second
        assert len(second["input"]) >= 3
        assert resource.connect_kwargs[1]["extra_headers"]["x-codex-routing-hint"] == (
            "model=gpt-other" if change == "model" else f"model={original_name};tier=priority"
        )
    finally:
        await http.aclose()
