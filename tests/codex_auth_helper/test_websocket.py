from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import httpx2
import openai.lib._websocket as sdk_websocket
import pytest
from codex_auth_helper import (
    CodexAsyncOpenAI,
    CodexResponsesConnectionError,
    CodexResponsesModel,
    CodexResponsesProtocolError,
    CodexResponsesTransportEvent,
)
from codex_auth_helper import client as helper_client
from openai.resources.responses import AsyncResponses
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseCreatedEvent,
    ResponseFunctionToolCall,
    ResponseOutputItemAddedEvent,
    ResponseTextDeltaEvent,
)
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters, ToolDefinition
from pydantic_ai.models.openai import OpenAIResponsesModelSettings
from pydantic_ai.providers.openai import OpenAIProvider


class _StructuredOutput(BaseModel):
    value: int


class _TokenManager:
    current_account_id = "acct_test"

    async def get_access_token(self) -> str:
        return "test-access-token"


class _FailingTokenManager:
    current_account_id = "acct_test"

    async def get_access_token(self) -> str:
        raise RuntimeError("auth failed")


class _FakeConnection:
    def __init__(
        self,
        event_batches: list[list[Any]] | None = None,
        *,
        send_error: Exception | None = None,
    ) -> None:
        self.events: list[Any] = []
        self.event_batches = event_batches or []
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._connection = SimpleNamespace(close_code=None)
        self.send_error = send_error

    async def send(self, event: dict[str, Any]) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(event)
        if self.event_batches:
            self.events.extend(self.event_batches.pop(0))

    def __aiter__(self) -> Any:
        return self._events()

    async def _events(self) -> Any:
        while self.events:
            yield self.events.pop(0)

    async def close(self) -> None:
        self.closed = True
        self._connection.close_code = 1000


class _FakeManager:
    def __init__(self, connection: _FakeConnection, *, error: Exception | None = None) -> None:
        self.connection = connection
        self.error = error
        self.closed = False

    async def __aenter__(self) -> _FakeConnection:
        if self.error is not None:
            raise self.error
        return self.connection

    async def __aexit__(self, *_exc_info: object) -> None:
        self.closed = True
        await self.connection.close()


class _FakeResponses:
    def __init__(
        self,
        *,
        connection_error: Exception | None = None,
        event_batches: list[list[Any]] | None = None,
        send_error: Exception | None = None,
    ) -> None:
        self.connection_error = connection_error
        self.event_batches = event_batches or []
        self.send_error = send_error
        self.connections: list[_FakeConnection] = []
        self.connect_kwargs: list[dict[str, Any]] = []
        self.http_calls: list[dict[str, Any]] = []

    def connect(self, **kwargs: Any) -> _FakeManager:
        connection = _FakeConnection(
            self.event_batches,
            send_error=self.send_error,
        )
        self.connections.append(connection)
        self.connect_kwargs.append(kwargs)
        return _FakeManager(connection, error=self.connection_error)

    async def create(self, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        self.http_calls.append(kwargs)
        return "http", kwargs


def _client(
    resource: _FakeResponses,
    *,
    observer: Any = None,
) -> tuple[CodexAsyncOpenAI, httpx2.AsyncClient]:
    http_client = httpx2.AsyncClient()
    client = CodexAsyncOpenAI(
        base_url="https://example.test/v1/",
        http_client=http_client,
        token_manager=cast(Any, _TokenManager()),
        owns_http_client=False,
        transport_observer=observer,
    )
    client._http_responses = cast(Any, resource)
    return client, http_client


def _model(
    client: CodexAsyncOpenAI,
    *,
    fallback: Any = "error",
) -> CodexResponsesModel:
    return CodexResponsesModel(
        "gpt-test",
        default_instructions="Keep the response precise.",
        provider=OpenAIProvider(openai_client=client),
        settings=cast(Any, {"openai_store": False}),
        connection="websocket",
        fallback=fallback,
    )


def _complete(response_id: str) -> Any:
    return SimpleNamespace(
        type="response.completed",
        response=SimpleNamespace(id=response_id),
    )


def _response(
    response_id: str,
    *,
    output: list[Any] | None = None,
    status: str,
) -> Response:
    return Response(
        id=response_id,
        created_at=0,
        model="gpt-test",
        object="response",
        output=output or [],
        parallel_tool_calls=True,
        tool_choice="auto",
        tools=[],
        status=cast(Any, status),
    )


def _text_events(response_id: str, text: str) -> list[Any]:
    return [
        ResponseCreatedEvent(
            response=_response(response_id, status="in_progress"),
            sequence_number=0,
            type="response.created",
        ),
        ResponseTextDeltaEvent(
            content_index=0,
            delta=text,
            item_id=f"msg_{response_id}",
            logprobs=[],
            output_index=0,
            sequence_number=1,
            type="response.output_text.delta",
        ),
        ResponseCompletedEvent(
            response=_response(response_id, status="completed"),
            sequence_number=2,
            type="response.completed",
        ),
    ]


def _tool_call_events(response_id: str) -> list[Any]:
    call = ResponseFunctionToolCall(
        arguments='{"value":2}',
        call_id="call_1",
        id="fc_1",
        name="double",
        status="completed",
        type="function_call",
    )
    return [
        ResponseCreatedEvent(
            response=_response(response_id, status="in_progress"),
            sequence_number=0,
            type="response.created",
        ),
        ResponseOutputItemAddedEvent(
            item=call,
            output_index=0,
            sequence_number=1,
            type="response.output_item.added",
        ),
        ResponseCompletedEvent(
            response=_response(response_id, output=[call], status="completed"),
            sequence_number=2,
            type="response.completed",
        ),
    ]


async def _drain(stream: Any) -> None:
    async with stream:
        async for _ in stream:
            pass


@pytest.mark.asyncio
async def test_http_default_delegates_final_request_unchanged() -> None:
    resource = _FakeResponses()
    client, http_client = _client(resource)
    request = {
        "model": "gpt-test",
        "input": [{"role": "user", "content": "hello"}],
        "stream": True,
        "store": False,
    }
    try:
        result = await cast(Any, client.responses).create(**request)
    finally:
        await http_client.aclose()

    assert result == ("http", request)
    assert resource.http_calls == [request]
    assert resource.connections == []


@pytest.mark.asyncio
async def test_explicit_http_session_reports_physical_request_metadata() -> None:
    resource = _FakeResponses()
    events: list[CodexResponsesTransportEvent] = []
    client, http_client = _client(resource, observer=events.append)
    try:
        async with client.responses_session(connection="http"):
            await cast(Any, client.responses).create(
                model="gpt-test",
                input="private prompt",
                stream=True,
            )
    finally:
        await http_client.aclose()

    assert len(events) == 1
    assert events[0].phase == "submitted"
    assert events[0].effective_connection == "http"
    assert events[0].request_bytes is not None
    assert "private prompt" not in repr(events)


@pytest.mark.asyncio
@pytest.mark.parametrize("connection", ["http", "websocket"])
async def test_disabled_observer_does_not_serialize_request_metrics(
    monkeypatch: pytest.MonkeyPatch,
    connection: Any,
) -> None:
    def unexpected_metrics(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("metrics must remain disabled")

    monkeypatch.setattr(helper_client, "_transport_event", unexpected_metrics)
    resource = _FakeResponses(event_batches=[[_complete("resp_one")]])
    client, http_client = _client(resource)
    try:
        async with client.responses_session(connection=connection) as info:
            result = await client.responses.create(model="gpt-test", input="hello", stream=True)
            if connection == "websocket":
                await _drain(result)
            assert info.observer_failures == 0
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
async def test_websocket_maps_final_request_and_reports_effective_connection() -> None:
    resource = _FakeResponses()
    client, http_client = _client(resource)
    try:
        async with client.responses_session(connection="websocket") as info:
            stream = await client.responses.create(
                model="gpt-test",
                input=[{"role": "user", "content": "hello"}],
                instructions="Be precise.",
                stream=True,
                store=False,
                extra_body={"custom": "value"},
            )
            resource.connections[0].events.append(_complete("resp_1"))
            await _drain(stream)

            assert info.effective_connection == "websocket"
            assert info.fallback_used is False
    finally:
        await http_client.aclose()

    assert resource.connections[0].sent == [
        {
            "type": "response.create",
            "model": "gpt-test",
            "input": [{"role": "user", "content": "hello"}],
            "instructions": "Be precise.",
            "store": False,
            "custom": "value",
        }
    ]
    headers = resource.connect_kwargs[0]["extra_headers"]
    assert headers["Authorization"] == "Bearer test-access-token"
    assert headers["ChatGPT-Account-Id"] == "acct_test"
    assert resource.connect_kwargs[0]["websocket_connection_options"] == {"close_timeout": 0.25}


@pytest.mark.asyncio
async def test_sdk_forwards_bounded_close_without_limiting_request_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connected: list[dict[str, Any]] = []
    sent: list[str | bytes] = []
    closed = False

    class Wire:
        async def send(self, data: str | bytes) -> None:
            sent.append(data)

        async def close(self, *, code: int, reason: str) -> None:
            nonlocal closed
            closed = True

    async def connect(url: str, **kwargs: Any) -> Wire:
        assert url == "wss://example.test/v1/responses"
        connected.append(kwargs)
        return Wire()

    monkeypatch.setattr(sdk_websocket, "_WebSocketConnect", connect)
    client, http_client = _client(_FakeResponses())
    client._http_responses = AsyncResponses(client)
    try:
        async with client.responses_session(connection="websocket"):
            stream = await client.responses.create(model="gpt-test", input="hello", stream=True)
            async with stream:
                pass
        assert client.current_responses_session() is None
    finally:
        await http_client.aclose()

    assert len(sent) == 1
    assert closed
    assert len(connected) == 1
    assert connected[0]["close_timeout"] == 0.25
    assert "timeout" not in connected[0]
    assert "open_timeout" not in connected[0]


@pytest.mark.asyncio
async def test_cancelling_run_closes_stream_and_restores_session() -> None:
    resource = _FakeResponses()
    client, http_client = _client(resource)
    ready = asyncio.Event()
    blocked = asyncio.Event()

    async def consume() -> None:
        try:
            async with client.responses_session(connection="websocket"):
                stream = await client.responses.create(model="gpt-test", input="hello", stream=True)
                async with stream:
                    ready.set()
                    await blocked.wait()
        finally:
            assert client.current_responses_session() is None

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(ready.wait(), timeout=1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1.0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await http_client.aclose()

    assert len(resource.connections) == 1
    assert resource.connections[0].closed
    assert resource.http_calls == []


@pytest.mark.asyncio
async def test_transport_observer_reports_content_free_physical_attempts() -> None:
    resource = _FakeResponses()
    events: list[CodexResponsesTransportEvent] = []
    client, http_client = _client(resource, observer=events.append)
    try:
        async with client.responses_session(connection="websocket"):
            stream = await client.responses.create(
                model="gpt-test",
                input=[{"role": "user", "content": "private prompt"}],
                stream=True,
            )
            resource.connections[0].events.append(_complete("resp_private"))
            await _drain(stream)
    finally:
        await http_client.aclose()

    assert [event.phase for event in events] == ["submitted", "completed"]
    assert events[0].effective_connection == "websocket"
    assert events[0].attempt == 1
    assert events[0].input_item_count == 1
    assert events[0].input_bytes is not None
    assert events[0].request_bytes is not None
    assert events[0].continuation is False
    assert "private prompt" not in repr(events)
    assert "test-access-token" not in repr(events)
    assert "acct_test" not in repr(events)


@pytest.mark.asyncio
async def test_transport_observer_failure_does_not_change_model_execution() -> None:
    resource = _FakeResponses()

    def observer(_event: CodexResponsesTransportEvent) -> None:
        raise RuntimeError("observer failed")

    client, http_client = _client(resource, observer=observer)
    try:
        async with client.responses_session(connection="websocket") as info:
            stream = await client.responses.create(model="gpt-test", input="hello", stream=True)
            resource.connections[0].events.append(_complete("resp_1"))
            await _drain(stream)
            assert info.observer_failures == 2
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
async def test_model_continues_only_an_unchanged_prefix_and_resends_instructions() -> None:
    resource = _FakeResponses()
    transport_events: list[CodexResponsesTransportEvent] = []
    client, http_client = _client(resource, observer=transport_events.append)
    model = _model(client)
    parameters = ModelRequestParameters()
    settings = cast(OpenAIResponsesModelSettings, dict(model.settings or {}))
    first_request = ModelRequest(
        parts=[UserPromptPart("first")],
        instructions="Keep the response precise.",
    )
    try:
        async with model.responses_session():
            first_stream = await model._responses_create(
                [first_request],
                True,
                settings,
                parameters,
            )
            resource.connections[0].events.append(_complete("resp_1"))
            await _drain(first_stream)

            first_response = ModelResponse(
                parts=[TextPart("one")],
                provider_name="openai",
                provider_response_id="resp_1",
            )
            second_request = ModelRequest(parts=[UserPromptPart("second")])
            second_stream = await model._responses_create(
                [first_request, first_response, second_request],
                True,
                settings,
                parameters,
            )
            resource.connections[0].events.append(_complete("resp_2"))
            await _drain(second_stream)
    finally:
        await http_client.aclose()

    first_event, second_event = resource.connections[0].sent
    assert "previous_response_id" not in first_event
    assert second_event["previous_response_id"] == "resp_1"
    assert second_event["input"] == [{"role": "user", "content": "second"}]
    assert second_event["instructions"] == "Keep the response precise."
    submitted = [event for event in transport_events if event.phase == "submitted"]
    assert [event.continuation_reason for event in submitted] == [
        "no_completed_response",
        "append_only",
    ]
    assert submitted[1].acknowledged_message_count == 1
    assert submitted[1].continuation is True


@pytest.mark.asyncio
async def test_native_pydantic_agent_parses_text_and_continues_after_tool_call() -> None:
    resource = _FakeResponses(
        event_batches=[
            _tool_call_events("resp_tool"),
            _text_events("resp_final", "done"),
        ]
    )
    client, http_client = _client(resource)
    model = _model(client)
    agent = Agent(model)

    @agent.tool_plain
    def double(value: int) -> int:
        return value * 2

    try:
        async with model.responses_session():
            result = await agent.run("Double two, then finish.")
    finally:
        await http_client.aclose()

    assert result.output == "done"
    first_event, second_event = resource.connections[0].sent
    assert "previous_response_id" not in first_event
    assert second_event["previous_response_id"] == "resp_tool"
    assert second_event["instructions"] == "Keep the response precise."
    assert [item["type"] for item in second_event["input"]] == ["function_call_output"]


@pytest.mark.asyncio
async def test_native_pydantic_agent_parses_structured_output() -> None:
    resource = _FakeResponses(event_batches=[_text_events("resp_structured", '{"value": 4}')])
    client, http_client = _client(resource)
    model = _model(client)
    agent = Agent(model, output_type=_StructuredOutput)

    try:
        async with model.responses_session():
            result = await agent.run("Return four.")
    finally:
        await http_client.aclose()

    assert result.output == _StructuredOutput(value=4)


@pytest.mark.asyncio
async def test_native_output_retry_continues_without_replaying_history() -> None:
    resource = _FakeResponses(
        event_batches=[
            _text_events("resp_invalid", "not-json"),
            _text_events("resp_valid", '{"value": 4}'),
        ]
    )
    client, http_client = _client(resource)
    model = _model(client)
    agent = Agent(model, output_type=_StructuredOutput)

    try:
        async with model.responses_session():
            result = await agent.run("Return four.")
    finally:
        await http_client.aclose()

    assert result.output == _StructuredOutput(value=4)
    assert len(resource.connections[0].sent) == 2
    retry_event = resource.connections[0].sent[1]
    assert retry_event["previous_response_id"] == "resp_invalid"
    assert len(retry_event["input"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("in_place", [False, True])
async def test_edited_prefix_resets_continuation_and_replays_full_history(in_place: bool) -> None:
    resource = _FakeResponses()
    client, http_client = _client(resource)
    model = _model(client)
    parameters = ModelRequestParameters()
    settings = cast(OpenAIResponsesModelSettings, dict(model.settings or {}))
    original = ModelRequest(parts=[UserPromptPart("original")])
    try:
        async with model.responses_session():
            first_stream = await model._responses_create([original], True, settings, parameters)
            resource.connections[0].events.append(_complete("resp_1"))
            await _drain(first_stream)

            if in_place:
                assert isinstance(original.parts[0], UserPromptPart)
                original.parts[0].content = "edited"
                edited = original
            else:
                edited = ModelRequest(parts=[UserPromptPart("edited")])
            response = ModelResponse(
                parts=[TextPart("one")],
                provider_name="openai",
                provider_response_id="resp_1",
            )
            next_request = ModelRequest(parts=[UserPromptPart("next")])
            second_stream = await model._responses_create(
                [edited, response, next_request], True, settings, parameters
            )
            resource.connections[0].events.append(_complete("resp_2"))
            await _drain(second_stream)
    finally:
        await http_client.aclose()

    second_event = resource.connections[0].sent[1]
    assert "previous_response_id" not in second_event
    assert [item["content"] for item in second_event["input"]] == [
        "edited",
        "one",
        "next",
    ]


@pytest.mark.asyncio
async def test_changed_request_contract_resets_continuation() -> None:
    resource = _FakeResponses()
    client, http_client = _client(resource)
    model = _model(client)
    parameters = ModelRequestParameters()
    first_settings = cast(OpenAIResponsesModelSettings, dict(model.settings or {}))
    changed_settings = cast(
        OpenAIResponsesModelSettings,
        {**first_settings, "temperature": 0.25},
    )
    first_request = ModelRequest(parts=[UserPromptPart("first")])
    try:
        async with model.responses_session():
            first_stream = await model._responses_create(
                [first_request], True, first_settings, parameters
            )
            resource.connections[0].events.append(_complete("resp_1"))
            await _drain(first_stream)

            first_response = ModelResponse(
                parts=[TextPart("one")],
                provider_name="openai",
                provider_response_id="resp_1",
            )
            second_stream = await model._responses_create(
                [first_request, first_response, ModelRequest(parts=[UserPromptPart("next")])],
                True,
                changed_settings,
                parameters,
            )
            resource.connections[0].events.append(_complete("resp_2"))
            await _drain(second_stream)
    finally:
        await http_client.aclose()

    second_event = resource.connections[0].sent[1]
    assert "previous_response_id" not in second_event
    assert [item["content"] for item in second_event["input"]] == [
        "first",
        "one",
        "next",
    ]


@pytest.mark.asyncio
async def test_reordered_tools_reset_continuation() -> None:
    resource = _FakeResponses()
    client, http_client = _client(resource)
    model = _model(client)
    settings = cast(OpenAIResponsesModelSettings, dict(model.settings or {}))
    alpha = ToolDefinition(name="alpha", parameters_json_schema={"type": "object"})
    beta = ToolDefinition(name="beta", parameters_json_schema={"type": "object"})
    first_parameters = ModelRequestParameters(function_tools=[alpha, beta])
    reordered_parameters = ModelRequestParameters(function_tools=[beta, alpha])
    first_request = ModelRequest(parts=[UserPromptPart("first")])
    try:
        async with model.responses_session():
            first_stream = await model._responses_create(
                [first_request], True, settings, first_parameters
            )
            resource.connections[0].events.append(_complete("resp_1"))
            await _drain(first_stream)

            first_response = ModelResponse(
                parts=[TextPart("one")],
                provider_name="openai",
                provider_response_id="resp_1",
            )
            second_stream = await model._responses_create(
                [first_request, first_response, ModelRequest(parts=[UserPromptPart("next")])],
                True,
                settings,
                reordered_parameters,
            )
            resource.connections[0].events.append(_complete("resp_2"))
            await _drain(second_stream)
    finally:
        await http_client.aclose()

    second_event = resource.connections[0].sent[1]
    assert "previous_response_id" not in second_event
    assert [tool["name"] for tool in second_event["tools"]] == ["beta", "alpha"]


@pytest.mark.asyncio
async def test_handshake_failure_is_strict_or_safely_falls_back() -> None:
    strict_resource = _FakeResponses(connection_error=OSError("private endpoint detail"))
    strict_events: list[CodexResponsesTransportEvent] = []
    strict_client, strict_http = _client(strict_resource, observer=strict_events.append)
    try:
        async with strict_client.responses_session(connection="websocket"):
            with pytest.raises(CodexResponsesConnectionError) as exc_info:
                await strict_client.responses.create(model="gpt-test", input="hello", stream=True)
    finally:
        await strict_http.aclose()
    assert "private endpoint detail" not in str(exc_info.value)
    assert strict_events[0].phase == "failed"
    assert strict_events[0].failure_category == "handshake"

    fallback_resource = _FakeResponses(connection_error=OSError("offline"))
    fallback_events: list[CodexResponsesTransportEvent] = []
    fallback_client, fallback_http = _client(
        fallback_resource,
        observer=fallback_events.append,
    )
    try:
        async with fallback_client.responses_session(
            connection="websocket",
            fallback="http",
        ) as info:
            result = await cast(Any, fallback_client.responses).create(
                model="gpt-test",
                input="hello",
                stream=True,
            )
            assert result[0] == "http"
            assert info.effective_connection == "http"
            assert info.fallback_used is True
    finally:
        await fallback_http.aclose()
    assert fallback_events[0].phase == "fallback"
    assert fallback_events[0].effective_connection == "http"


@pytest.mark.asyncio
async def test_auth_failure_never_falls_back_to_http() -> None:
    resource = _FakeResponses()
    client, http_client = _client(resource)
    client.token_manager = cast(Any, _FailingTokenManager())
    try:
        async with client.responses_session(
            connection="websocket",
            fallback="http",
        ):
            with pytest.raises(RuntimeError, match="auth failed"):
                await client.responses.create(
                    model="gpt-test",
                    input="hello",
                    stream=True,
                )
    finally:
        await http_client.aclose()

    assert resource.connections == []
    assert resource.http_calls == []


@pytest.mark.asyncio
async def test_request_send_failure_is_not_replayed_over_http() -> None:
    resource = _FakeResponses(send_error=OSError("delivery unknown"))
    events: list[CodexResponsesTransportEvent] = []
    client, http_client = _client(resource, observer=events.append)
    try:
        async with client.responses_session(
            connection="websocket",
            fallback="http",
        ):
            with pytest.raises(CodexResponsesProtocolError, match="not replayed"):
                await client.responses.create(
                    model="gpt-test",
                    input="hello",
                    stream=True,
                )
    finally:
        await http_client.aclose()

    assert resource.connections[0].closed is True
    assert resource.http_calls == []
    assert events[0].phase == "failed"
    assert events[0].failure_category == "send"


@pytest.mark.asyncio
async def test_per_request_timeout_is_rejected_instead_of_ignored() -> None:
    resource = _FakeResponses()
    client, http_client = _client(resource)
    try:
        async with client.responses_session(connection="websocket"):
            with pytest.raises(CodexResponsesProtocolError, match="Per-request timeouts"):
                await client.responses.create(
                    model="gpt-test",
                    input="hello",
                    stream=True,
                    timeout=1.0,
                )
    finally:
        await http_client.aclose()

    assert resource.connections == []


@pytest.mark.asyncio
async def test_unknown_previous_response_id_is_rejected_before_connecting() -> None:
    resource = _FakeResponses()
    client, http_client = _client(resource)
    try:
        async with client.responses_session(connection="websocket"):
            with pytest.raises(CodexResponsesProtocolError, match="not owned"):
                await client.responses.create(
                    model="gpt-test",
                    input="hello",
                    previous_response_id="resp_unknown",
                    stream=True,
                )
    finally:
        await http_client.aclose()

    assert resource.connections == []


@pytest.mark.asyncio
async def test_early_stream_close_invalidates_and_closes_connection() -> None:
    resource = _FakeResponses()
    client, http_client = _client(resource)
    try:
        async with client.responses_session(connection="websocket"):
            stream = await client.responses.create(model="gpt-test", input="hello", stream=True)
            connection = resource.connections[0]
            connection.events.append(SimpleNamespace(type="response.created"))
            async with stream:
                async for _ in stream:
                    break
            assert connection.closed is True
            state = client.current_responses_session()
            assert state is not None
            assert state.last_response_id is None
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
async def test_socket_eof_before_terminal_event_invalidates_the_session() -> None:
    resource = _FakeResponses()
    client, http_client = _client(resource)
    try:
        async with client.responses_session(connection="websocket"):
            stream = await client.responses.create(model="gpt-test", input="hello", stream=True)
            with pytest.raises(CodexResponsesProtocolError, match="terminal response"):
                await _drain(stream)
            state = client.current_responses_session()
            assert state is not None
            assert state.last_response_id is None
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
async def test_stopping_on_completed_event_preserves_connection_and_continuation() -> None:
    resource = _FakeResponses()
    events: list[CodexResponsesTransportEvent] = []
    client, http_client = _client(resource, observer=events.append)
    try:
        async with client.responses_session(connection="websocket"):
            stream = await client.responses.create(model="gpt-test", input="first", stream=True)
            connection = resource.connections[0]
            connection.events.append(_complete("resp_first"))
            async with stream:
                async for event in stream:
                    if event.type == "response.completed":
                        break
            assert connection.closed is False
            state = client.current_responses_session()
            assert state is not None
            assert state.last_response_id == "resp_first"
            second = await client.responses.create(
                model="gpt-test", input="next", stream=True, previous_response_id="resp_first"
            )
            connection.events.append(_complete("resp_second"))
            await _drain(second)
    finally:
        await http_client.aclose()
    assert len(resource.connections) == 1
    assert [event.phase for event in events] == ["submitted", "completed", "submitted", "completed"]


@pytest.mark.asyncio
async def test_model_reconnects_before_sending_after_known_socket_close() -> None:
    resource = _FakeResponses(
        event_batches=[_text_events("resp_one", "original"), _text_events("resp_two", "done")]
    )
    client, http_client = _client(resource)
    model = _model(client)
    agent = Agent(model)
    try:
        async with model.responses_session():
            first = await agent.run("Remember the original record.")
            resource.connections[0]._connection = SimpleNamespace(close_code=1000)
            second = await agent.run("Next.", message_history=first.all_messages())
    finally:
        await http_client.aclose()
    assert second.output == "done"
    assert len(resource.connections) == 2
    event = resource.connections[1].sent[0]
    assert "previous_response_id" not in event
    assert [item["content"] for item in event["input"]] == [
        "Remember the original record.",
        "original",
        "Next.",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_handshake_options_are_honored_across_native_calls(changed: bool) -> None:
    resource = _FakeResponses(
        event_batches=[_text_events("resp_one", "original"), _text_events("resp_two", "done")]
    )
    client, http_client = _client(resource)
    model = _model(client)
    agent = Agent(model)
    settings = {"extra_headers": {"x-probe-route": "a"}}
    try:
        async with model.responses_session():
            first = await agent.run(
                "Remember the original record.", model_settings=cast(Any, settings)
            )
            if changed:
                settings["extra_headers"]["x-probe-route"] = "b"
            second = await agent.run(
                "Next.", message_history=first.all_messages(), model_settings=cast(Any, settings)
            )
    finally:
        await http_client.aclose()
    assert second.output == "done"
    assert len(resource.connections) == (2 if changed else 1)
    assert resource.connect_kwargs[-1]["extra_headers"]["x-probe-route"] == (
        "b" if changed else "a"
    )
    event = resource.connections[-1].sent[-1]
    if changed:
        assert "previous_response_id" not in event
        assert len(event["input"]) == 3
    else:
        assert event["previous_response_id"] == "resp_one"
        assert event["input"] == [{"role": "user", "content": "Next."}]

    assert resource.connections[0].closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["response.failed", "response.incomplete"])
async def test_non_completed_terminal_event_is_not_checkpointed(event_type: str) -> None:
    resource = _FakeResponses()
    client, http_client = _client(resource)
    try:
        async with client.responses_session(connection="websocket"):
            stream = await client.responses.create(model="gpt-test", input="hello", stream=True)
            resource.connections[0].events.append(SimpleNamespace(type=event_type))
            await _drain(stream)
            state = client.current_responses_session()
            assert state is not None
            assert state.last_response_id is None
    finally:
        await http_client.aclose()

    assert resource.connections[0].closed is True


@pytest.mark.asyncio
async def test_shared_client_uses_independent_task_local_sessions() -> None:
    resource = _FakeResponses()
    client, http_client = _client(resource)

    async def run(label: str) -> str:
        async with client.responses_session(connection="websocket"):
            stream = await client.responses.create(model="gpt-test", input=label, stream=True)
            connection = resource.connections[-1]
            connection.events.append(_complete(f"resp_{label}"))
            await _drain(stream)
            state = client.current_responses_session()
            assert state is not None
            return state.last_response_id or ""

    try:
        results = await asyncio.gather(run("a"), run("b"))
    finally:
        await http_client.aclose()

    assert results == ["resp_a", "resp_b"]
    assert len(resource.connections) == 2
    assert {connection.sent[0]["input"] for connection in resource.connections} == {"a", "b"}


@pytest.mark.asyncio
async def test_child_task_does_not_inherit_parent_socket_ownership() -> None:
    resource = _FakeResponses()
    client, http_client = _client(resource)

    async def complete(label: str, *, isolate: bool = False) -> None:
        async with client.responses_session(
            connection="websocket",
            isolate=isolate,
        ):
            stream = await client.responses.create(model="gpt-test", input=label, stream=True)
            connection = resource.connections[-1]
            connection.events.append(_complete(f"resp_{label}"))
            await _drain(stream)

    try:
        async with client.responses_session(connection="websocket"):
            await complete("parent")
            await asyncio.create_task(complete("child", isolate=True))
    finally:
        await http_client.aclose()

    assert len(resource.connections) == 2
    assert resource.connections[0].sent[0]["input"] == "parent"
    assert resource.connections[1].sent[0]["input"] == "child"


@pytest.mark.asyncio
async def test_session_rejects_parallel_in_flight_responses() -> None:
    resource = _FakeResponses()
    client, http_client = _client(resource)
    try:
        async with client.responses_session(connection="websocket"):
            stream = await client.responses.create(model="gpt-test", input="first", stream=True)
            with pytest.raises(CodexResponsesProtocolError, match="in-flight"):
                await client.responses.create(model="gpt-test", input="second", stream=True)
            await stream.__aexit__(None, None, None)
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("option", ["extra_headers", "extra_query"])
@pytest.mark.parametrize("changed", [False, True])
async def test_raw_connection_option_snapshots_preserve_or_replace_socket(
    option: str, changed: bool
) -> None:
    resource = _FakeResponses()
    client, http_client = _client(resource)
    options = {"x-probe-route": "a"}
    try:
        async with client.responses_session(connection="websocket"):
            for index in range(2):
                if index and changed:
                    options["x-probe-route"] = "b"
                stream = await client.responses.create(
                    model="gpt-test",
                    input="full input",
                    stream=True,
                    extra_headers=options if option == "extra_headers" else None,
                    extra_query=options if option == "extra_query" else None,
                )
                resource.connections[-1].events.append(_complete(f"resp_{index}"))
                await _drain(stream)
    finally:
        await http_client.aclose()
    assert len(resource.connections) == (2 if changed else 1)
    assert resource.connect_kwargs[-1][option]["x-probe-route"] == ("b" if changed else "a")


@pytest.mark.asyncio
async def test_raw_suffix_is_rejected_when_connection_requires_renewal() -> None:
    resource = _FakeResponses()
    client, http_client = _client(resource)
    try:
        async with client.responses_session(connection="websocket"):
            stream = await client.responses.create(model="gpt-test", input="first", stream=True)
            connection = resource.connections[0]
            connection.events.append(_complete("resp_first"))
            await _drain(stream)
            connection._connection.close_code = 1000
            with pytest.raises(CodexResponsesProtocolError, match="Send full input"):
                await client.responses.create(
                    model="gpt-test", input="next", stream=True, previous_response_id="resp_first"
                )
            assert len(connection.sent) == 1
            assert len(resource.connections) == 1
            assert resource.http_calls == []
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
async def test_reconnect_failure_does_not_fall_back_after_established_websocket() -> None:
    resource = _FakeResponses()
    client, http_client = _client(resource)
    try:
        async with client.responses_session(connection="websocket", fallback="http"):
            stream = await client.responses.create(model="gpt-test", input="first", stream=True)
            resource.connections[0].events.append(_complete("resp_first"))
            await _drain(stream)
            resource.connections[0]._connection.close_code = 1000
            resource.connection_error = OSError("reconnection refused")
            with pytest.raises(CodexResponsesConnectionError):
                await client.responses.create(model="gpt-test", input="full input", stream=True)
            assert resource.http_calls == []
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
async def test_connection_replacement_keeps_in_flight_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready, release = asyncio.Event(), asyncio.Event()
    resource = _FakeResponses()
    client, http_client = _client(resource)
    try:
        async with client.responses_session(connection="websocket"):
            stream = await client.responses.create(model="gpt-test", input="first", stream=True)
            connection = resource.connections[0]
            connection.events.append(_complete("resp_first"))
            await _drain(stream)
            original_close = connection.close

            async def delayed_close() -> None:
                ready.set()
                await release.wait()
                await original_close()

            monkeypatch.setattr(connection, "close", delayed_close)
            replacement = asyncio.create_task(
                client.responses.create(
                    model="gpt-test",
                    input="full input",
                    stream=True,
                    extra_headers={"x-probe-route": "b"},
                )
            )
            try:
                await asyncio.wait_for(ready.wait(), timeout=1.0)
                with pytest.raises(CodexResponsesProtocolError, match="in-flight"):
                    await client.responses.create(model="gpt-test", input="overlap", stream=True)
            finally:
                release.set()
                second = await asyncio.wait_for(replacement, timeout=1.0)
                resource.connections[-1].events.append(_complete("resp_second"))
                await _drain(second)
    finally:
        await http_client.aclose()
    assert [len(connection.sent) for connection in resource.connections] == [1, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("edit", [False, True])
async def test_latest_model_output_is_part_of_continuation_boundary(edit: bool) -> None:
    resource = _FakeResponses(
        event_batches=[_text_events("resp_one", "original"), _text_events("resp_two", "done")]
    )
    client, http_client = _client(resource)
    model = _model(client)
    agent = Agent(model)
    try:
        async with model.responses_session():
            first = await agent.run("Say original.")
            history = first.all_messages()
            assert isinstance(history[-1], ModelResponse)
            part = history[-1].parts[0]
            assert isinstance(part, TextPart)
            if edit:
                part.content = "edited"
            await agent.run("Next.", message_history=history)
    finally:
        await http_client.aclose()
    second_event = resource.connections[0].sent[1]
    if edit:
        assert "previous_response_id" not in second_event
        assert any(item.get("content") == "edited" for item in second_event["input"])
    else:
        assert second_event["previous_response_id"] == "resp_one"
        assert second_event["input"] == [{"role": "user", "content": "Next."}]


@pytest.mark.asyncio
async def test_uncopyable_history_uses_full_request_without_breaking_generation() -> None:
    class UncopyableText(str):
        def __deepcopy__(self, memo: dict[int, Any]) -> Any:
            raise TypeError("not copyable")

    resource = _FakeResponses(
        event_batches=[_text_events("resp_one", "first"), _text_events("resp_two", "done")]
    )
    client, http_client = _client(resource)
    model = _model(client)
    agent = Agent(model)
    try:
        async with model.responses_session():
            first = await agent.run(UncopyableText("First."))
            second = await agent.run("Next.", message_history=first.all_messages())
    finally:
        await http_client.aclose()
    assert second.output == "done"
    assert "previous_response_id" not in resource.connections[0].sent[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["handshake", "send"])
async def test_concurrent_submit_is_rejected_before_first_send_finishes(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    ready = asyncio.Event()
    release = asyncio.Event()
    resource = _FakeResponses()
    client, http_client = _client(resource)
    original_token = client.token_manager.get_access_token
    original_send = _FakeConnection.send

    async def token() -> str:
        ready.set()
        await release.wait()
        return await original_token()

    async def send(self: _FakeConnection, event: dict[str, Any]) -> None:
        ready.set()
        await release.wait()
        await original_send(self, event)

    if phase == "handshake":
        monkeypatch.setattr(client.token_manager, "get_access_token", token)
    else:
        monkeypatch.setattr(_FakeConnection, "send", send)
    try:
        async with client.responses_session(connection="websocket"):
            first = asyncio.create_task(
                client.responses.create(model="gpt-test", input="first", stream=True)
            )
            try:
                await asyncio.wait_for(ready.wait(), timeout=1.0)
                with pytest.raises(CodexResponsesProtocolError, match="in-flight"):
                    await asyncio.wait_for(
                        client.responses.create(model="gpt-test", input="second", stream=True),
                        timeout=0.25,
                    )
            finally:
                release.set()
                stream = await asyncio.wait_for(first, timeout=1.0)
                await stream.__aexit__(None, None, None)
    finally:
        await http_client.aclose()
    assert len(resource.connections) == 1
    assert len(resource.connections[0].sent) == 1
