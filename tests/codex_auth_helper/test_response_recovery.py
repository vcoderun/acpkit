from __future__ import annotations as _annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx2
import pytest
from codex_auth_helper import (
    CodexAuthAccountMismatchError,
    CodexAuthConfig,
    CodexAuthRefreshError,
    CodexResponsesConnection,
    CodexResponsesModel,
    CodexResponsesProtocolError,
    create_codex_chat_openai,
)
from langchain_core.messages import HumanMessage, ToolMessage
from pydantic_ai import Agent, Tool
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.native_tools import WebSearchTool

from .support import write_auth_file
from .test_langchain import _model as langchain_model
from .test_websocket import (
    _client,
    _FakeConnection,
    _FakeManager,
    _FakeResponses,
    _model,
    _text_events,
    _tool_call_events,
)


class FaultConnection(_FakeConnection):
    async def _events(self) -> Any:
        while self.events:
            event = self.events.pop(0)
            if isinstance(event, tuple):
                entered, release = event
                entered.set()
                await release.wait()
                continue
            if isinstance(event, BaseException):
                raise event
            yield event


class HTTPStream(FaultConnection):
    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()


class FaultResponses(_FakeResponses):
    http_text: str = "recovered"

    def connect(self, **kwargs: Any) -> _FakeManager:
        connection = FaultConnection(self.event_batches, send_error=self.send_error)
        self.send_error = None
        self.connections.append(connection)
        self.connect_kwargs.append(kwargs)
        return _FakeManager(connection, error=self.connection_error)

    async def create(self, **kwargs: Any) -> Any:
        self.http_calls.append(kwargs)
        stream = HTTPStream()
        stream.events = _text_events("resp_http", self.http_text)
        return stream


@pytest.fixture(autouse=True)
def no_recovery_delays(monkeypatch: pytest.MonkeyPatch) -> None:
    async def sleep(_: float) -> None:
        return None

    monkeypatch.setattr("codex_auth_helper.client.asyncio.sleep", sleep)


@pytest.mark.asyncio
@pytest.mark.parametrize("framework", ["pydantic", "langchain"])
@pytest.mark.parametrize("failures", [1, 3])
async def test_recovery_preserves_completed_tool_once_and_discards_partial(
    framework: str, failures: int
) -> None:
    resource = FaultResponses(
        event_batches=[
            _tool_call_events("resp_tool"),
            *[
                [
                    *_text_events("resp_lost", "discard me")[:-1],
                    ConnectionResetError("synthetic drop"),
                ]
                for _ in range(failures)
            ],
            _text_events("resp_final", "4"),
        ]
    )
    resource.http_text = "4"
    observed = []
    client, http = _client(resource, observer=observed.append)
    calls: list[int] = []

    def double(value: int) -> int:
        calls.append(value)
        return value * 2

    try:
        if framework == "pydantic":
            py_model = _model(client, fallback="http")
            async with py_model.responses_session():
                result = await Agent(py_model, tools=[Tool(double, takes_ctx=False)]).run(
                    "double two"
                )
            assert result.output == "4"
            assert "discard me" not in str(result.all_messages())
        else:
            model = langchain_model(client, responses_fallback="http")
            bound = model.bind_tools([double])
            async with model.responses_session():
                messages: list[Any] = [HumanMessage("double two")]
                first = await bound.ainvoke(messages)
                call = first.tool_calls[0]
                messages.extend(
                    [first, ToolMessage(str(double(**call["args"])), tool_call_id=call["id"])]
                )
                result = await bound.ainvoke(messages)
            assert "discard me" not in str(result.content)
            assert "4" in str(result.content)
        assert calls == [2]
        assert len(resource.connections) == min(failures + 1, 3)
        assert bool(resource.http_calls) == (failures == 3)
        if resource.http_calls:
            http_request = resource.http_calls[0]
            assert not isinstance(http_request.get("previous_response_id"), str)
            assert any(x.get("type") == "function_call_output" for x in http_request["input"])
            assert any(x.get("role") == "user" for x in http_request["input"])
        initial = resource.connections[0].sent
        retried = resource.connections[1].sent[0]
        assert initial[1]["previous_response_id"] == "resp_tool"
        assert "previous_response_id" not in retried
        assert any(x.get("type") == "function_call_output" for x in retried["input"])
        assert any(x.get("role") == "user" for x in retried["input"])
        assert any(x.phase == "retry" for x in observed)
        assert any(x.failure_category == "receive" for x in observed)
        assert all(c.closed for c in resource.connections)
    finally:
        await http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failures", [1, 3])
async def test_langchain_factory_default_recovers(
    failures: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resource = FaultResponses(
        event_batches=[[ConnectionResetError("synthetic factory drop")] for _ in range(failures)]
        + [_text_events("resp_final", "recovered")]
    )
    client, http = _client(resource)
    monkeypatch.setattr("codex_auth_helper.factory.create_codex_async_openai", lambda **_: client)
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path, account_id="synthetic")
    model = create_codex_chat_openai(
        "gpt-5.6-luna",
        config=CodexAuthConfig(auth_path=auth_path),
        instructions="Synthetic test",
        connection="websocket",
        fallback="http",
    )
    try:
        assert "streaming" not in model.model_fields_set
        assert model._should_stream(async_api=True, stream=True)
        assert not model._should_stream(async_api=True)
        result = await model.ainvoke("test")
        assert "recovered" in str(result.content)
        assert len(resource.connections) == min(failures + 1, 3)
        assert bool(resource.http_calls) == (failures == 3)
    finally:
        model.root_client.close()
        await http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("framework", ["pydantic", "langchain"])
async def test_premature_eof_recovers(framework: str) -> None:
    resource = FaultResponses(
        event_batches=[
            _text_events("resp_lost", "partial")[:-1],
            _text_events("resp_final", "complete"),
        ]
    )
    client, http = _client(resource)
    try:
        if framework == "pydantic":
            result = await Agent(_model(client)).run("test")
            assert result.output == "complete"
        else:
            answer = await langchain_model(client).ainvoke("test")
            assert "complete" in str(answer.content)
            assert "partial" not in str(answer.content)
        assert len(resource.connections) == 2
    finally:
        await http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("framework", ["pydantic", "langchain"])
@pytest.mark.parametrize("fallback", ["error", "http"])
async def test_recovery_is_bounded_and_http_is_opt_in(framework: str, fallback: str) -> None:
    resource = FaultResponses(event_batches=[[ConnectionResetError("drop")] for _ in range(3)])
    client, http = _client(resource)
    model = (
        _model(client, fallback=fallback)
        if framework == "pydantic"
        else langchain_model(client, responses_fallback=fallback)
    )
    try:

        async def invoke() -> Any:
            if isinstance(model, CodexResponsesModel):
                return await model.request(
                    [ModelRequest(parts=[UserPromptPart("hello")])], None, ModelRequestParameters()
                )
            return await model.ainvoke("hello")

        if fallback == "error":
            with pytest.raises(ConnectionResetError):
                await invoke()
            assert not resource.http_calls
        else:
            result = await invoke()
            assert "recovered" in str(result)
            assert len(resource.http_calls) == 1
            assert "previous_response_id" not in resource.http_calls[0] or not isinstance(
                resource.http_calls[0]["previous_response_id"], str
            )
        assert len(resource.connections) == 3
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_pydantic_ambiguous_send_recovers_only_model_request() -> None:
    resource = FaultResponses(
        send_error=ConnectionResetError("send failed"),
        event_batches=[_text_events("resp_ok", "ok")],
    )
    client, http = _client(resource)
    try:
        result = await Agent(_model(client)).run("hello")
        assert result.output == "ok"
        assert len(resource.connections) == 2
    finally:
        await http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("framework", ["pydantic", "langchain"])
async def test_cancellation_never_replays(framework: str) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    resource = FaultResponses(event_batches=[[(entered, release)]])
    client, http = _client(resource)
    try:

        async def invoke() -> None:
            if framework == "pydantic":
                await Agent(_model(client)).run("hello")
            else:
                await langchain_model(client).ainvoke("hello")

        task = asyncio.create_task(invoke())
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(resource.connections) == 1
        assert resource.connections[0].closed
    finally:
        await http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("framework", ["pydantic", "langchain"])
async def test_provider_hosted_tools_prevent_blind_replay(framework: str) -> None:
    resource = FaultResponses(event_batches=[[ConnectionResetError("drop")]])
    client, http = _client(resource)
    try:
        with pytest.raises(ConnectionResetError):
            if framework == "pydantic":
                await _model(client, fallback="http").request(
                    [ModelRequest(parts=[UserPromptPart("search")])],
                    None,
                    ModelRequestParameters(native_tools=[WebSearchTool()]),
                )
            else:
                await (
                    langchain_model(client, responses_fallback="http")
                    .bind(tools=[{"type": "web_search"}])
                    .ainvoke("search")
                )
        assert len(resource.connections) == 1
        assert not resource.http_calls
    finally:
        await http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        CodexAuthAccountMismatchError("changed"),
        CodexAuthRefreshError("rejected"),
        PermissionError("denied"),
        CodexResponsesProtocolError("malformed"),
        ValueError("invalid"),
    ],
)
async def test_permanent_failures_do_not_trigger_recovery(failure: Exception) -> None:
    client, http = _client(FaultResponses())
    try:
        async with client.responses_session(connection="websocket", fallback="http") as info:
            state = client.current_responses_session()
            assert state is not None
            state.replay_safe = True
            info.effective_connection = "websocket"
            assert not await client.recover_response(failure, retries=0)
            assert not await client.recover_response(failure, retries=client.max_retries)
            assert info.effective_connection == "websocket"
    finally:
        await http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 408, 429, 500, 503])
async def test_status_failure_recovery_classification(status: int) -> None:
    request = httpx2.Request("GET", "https://example.test/responses")
    failure = httpx2.HTTPStatusError(
        "synthetic handshake failure",
        request=request,
        response=httpx2.Response(status, request=request),
    )
    events = []
    client, http = _client(FaultResponses(), observer=events.append)
    try:
        async with client.responses_session(connection="websocket") as info:
            state = client.current_responses_session()
            assert state is not None
            state.replay_safe = True
            info.effective_connection = "websocket"
            recovered = await client.recover_response(failure, retries=0)
            assert recovered is (status in {408, 429, 500, 503})
            assert [event.phase for event in events] == (["retry"] if recovered else [])
            if recovered:
                assert events[0].input_item_count == 0
                assert events[0].failure_category == "transport"
    finally:
        await http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("connection", ["http", "websocket"])
async def test_langchain_generation_without_callback_manager(
    connection: CodexResponsesConnection,
) -> None:
    resource = FaultResponses(event_batches=[_text_events("resp_complete", "recovered")])
    client, http = _client(resource)
    model = langchain_model(client)
    model.responses_connection = connection
    try:
        result = await model._agenerate([HumanMessage("hello")])
        assert result.generations[0].text == "recovered"
        assert len(resource.http_calls) == (1 if connection == "http" else 0)
        assert len(resource.connections) == (1 if connection == "websocket" else 0)
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_recovery_does_not_mutate_another_session() -> None:
    client, http = _client(FaultResponses())
    try:
        async with client.responses_session(connection="websocket", fallback="http") as parent:
            parent_state = client.current_responses_session()
            assert parent_state is not None
            parent_state.last_response_id = "parent-response"
            parent.effective_connection = "websocket"

            async def child() -> None:
                async with client.responses_session(
                    connection="websocket", fallback="http", isolate=True
                ) as info:
                    state = client.current_responses_session()
                    assert state is not None
                    state.replay_safe = True
                    assert await client.recover_response(
                        ConnectionResetError("drop"), retries=client.max_retries
                    )
                    assert info.effective_connection == "http"

            await asyncio.create_task(child())
            assert parent.effective_connection == "websocket"
            assert parent_state.last_response_id == "parent-response"
    finally:
        await http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("framework", ["pydantic", "langchain"])
async def test_exposed_stream_is_not_replayed(framework: str) -> None:
    resource = FaultResponses(
        event_batches=[[*_text_events("resp_lost", "partial")[:-1], ConnectionResetError("drop")]]
    )
    client, http = _client(resource)
    try:
        with pytest.raises(ConnectionResetError):
            if framework == "pydantic":
                async with _model(client).request_stream(
                    [ModelRequest(parts=[UserPromptPart("hello")])], None, ModelRequestParameters()
                ) as stream:
                    async for _ in stream:
                        pass
            else:
                async for _ in langchain_model(client).astream("hello"):
                    pass
        assert len(resource.connections) == 1
    finally:
        await http.aclose()
