from __future__ import annotations

import asyncio
from contextlib import aclosing
from types import SimpleNamespace
from typing import Any, cast

import pytest
from codex_auth_helper import CodexResponsesProtocolError
from codex_auth_helper import langchain as helper_langchain
from codex_auth_helper.langchain import CodexChatOpenAI
from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk
from pydantic import SecretStr

from .test_websocket import _client, _FakeResponses, _text_events, _tool_call_events


def _model(client: Any, **kwargs: Any) -> CodexChatOpenAI:
    return CodexChatOpenAI(
        model="gpt-5.6-luna",
        api_key=SecretStr("synthetic"),
        root_async_client=client,
        root_client=SimpleNamespace(),
        client=SimpleNamespace(),
        async_client=SimpleNamespace(),
        model_kwargs={"instructions": "Fallback", "prompt_cache_key": "stable"},
        reasoning={"effort": "high"},
        store=False,
        use_responses_api=True,
        output_version="responses/v1",
        responses_connection="websocket",
        **kwargs,
    )


def test_langchain_native_payload_and_continuation() -> None:
    async def run() -> None:
        resource = _FakeResponses(event_batches=[_text_events(f"resp_{i}", "ok") for i in range(3)])
        client, http = _client(resource)
        model = _model(client)
        try:
            async with model.responses_session():
                messages: list[Any] = [SystemMessage("Active instruction"), HumanMessage("first")]
                one = await model.ainvoke(messages)
                messages.extend([one, HumanMessage("second")])
                two = await model.ainvoke(messages)
                messages.extend([two, HumanMessage("third")])
                messages[1] = HumanMessage("edited first")
                await model.ainvoke(messages)
                sent = resource.connections[0].sent
                assert sent[0]["instructions"] == "Active instruction"
                assert sent[0]["store"] is False
                assert sent[0]["reasoning"] == {"effort": "high"}
                assert sent[0]["prompt_cache_key"] == "stable"
                assert all(x.get("role") != "system" for x in sent[0]["input"])
                assert sent[1]["previous_response_id"] == "resp_0"
                assert len(sent[1]["input"]) == 1
                assert "previous_response_id" not in sent[2]
                assert len(sent[2]["input"]) == 5
            assert resource.connections[0].closed
        finally:
            await http.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "change", ["instructions", "tools", "effort", "cache_key", "headers", "socket"]
)
def test_langchain_changed_contract_or_connection_sends_full(change: str) -> None:
    async def run() -> None:
        resource = _FakeResponses(event_batches=[_text_events(f"resp_{i}", "ok") for i in range(2)])
        client, http = _client(resource)
        model = _model(client)
        try:
            async with model.responses_session():
                history: list[Any] = [HumanMessage("one")]
                history.extend([await model.ainvoke(history), HumanMessage("two")])
                kwargs: dict[str, Any] = {}
                if change == "instructions":
                    history.insert(0, SystemMessage("Changed"))
                elif change == "tools":
                    kwargs["tools"] = [
                        {
                            "type": "function",
                            "name": "noop",
                            "parameters": {"type": "object", "properties": {}},
                        }
                    ]
                elif change == "effort":
                    kwargs["reasoning"] = {"effort": "low"}
                elif change == "cache_key":
                    kwargs["prompt_cache_key"] = "changed"
                elif change == "headers":
                    kwargs["extra_headers"] = {"x-synthetic": "changed"}
                else:
                    resource.connections[0]._connection.close_code = 1000
                await model.ainvoke(history, **kwargs)
                sent = [event for connection in resource.connections for event in connection.sent]
                assert "previous_response_id" not in sent[1]
                assert len(sent[1]["input"]) == 3
                assert len(resource.connections) == (2 if change in {"headers", "socket"} else 1)
        finally:
            await http.aclose()

    asyncio.run(run())


def test_langchain_tool_result_continuation() -> None:
    async def run() -> None:
        resource = _FakeResponses(
            event_batches=[_tool_call_events("resp_tool"), _text_events("resp_final", "4")]
        )
        client, http = _client(resource)
        model = _model(client)
        try:
            async with model.responses_session():
                history: list[Any] = [HumanMessage("double 2")]
                response = await model.ainvoke(history)
                assert response.tool_calls[0]["args"] == {"value": 2}
                history.extend([response, ToolMessage("4", tool_call_id="call_1")])
                await model.ainvoke(history)
                second = resource.connections[0].sent[1]
                assert second["previous_response_id"] == "resp_tool"
                assert second["input"] == [
                    {"type": "function_call_output", "call_id": "call_1", "output": "4"}
                ]
        finally:
            await http.aclose()

    asyncio.run(run())


def test_langchain_stream_early_close_resets_chain() -> None:
    async def run() -> None:
        resource = _FakeResponses(
            event_batches=[_text_events("resp_0", "one"), _text_events("resp_1", "two")]
        )
        client, http = _client(resource)
        model = _model(client)
        try:
            async with model.responses_session():
                async with aclosing(model.astream("one")) as stream:
                    await anext(stream)
                state = client.current_responses_session()
                assert state is not None and state.last_response_id is None
                assert not state.in_flight
                await model.ainvoke("two")
                assert len(resource.connections) == 2
                assert "previous_response_id" not in resource.connections[1].sent[0]
        finally:
            await http.aclose()

    asyncio.run(run())


def test_langchain_overlapping_request_does_not_reset_owner() -> None:
    async def run() -> None:
        resource = _FakeResponses(event_batches=[_text_events("resp_0", "ok")])
        client, http = _client(resource)
        model = _model(client)
        try:
            async with model.responses_session():
                async with aclosing(model.astream("one")) as stream:
                    await anext(stream)
                    with pytest.raises(CodexResponsesProtocolError, match="one in-flight"):
                        await model.ainvoke("overlap")
                    state = client.current_responses_session()
                    assert state is not None
                    assert state.in_flight
                    async for _chunk in stream:
                        pass
                assert state.last_response_id == "resp_0"
                assert len(resource.connections[0].sent) == 1
        finally:
            await http.aclose()

    asyncio.run(run())


def test_langchain_rejects_sync_websocket_and_foreign_response_ids() -> None:
    async def run() -> None:
        client, http = _client(_FakeResponses())
        model = _model(client)
        try:
            with pytest.raises(ValueError, match="ainvoke"):
                model.invoke("one")
            with pytest.raises(ValueError, match="owned"):
                await model.ainvoke("one", previous_response_id="resp_foreign")
        finally:
            await http.aclose()

    asyncio.run(run())


def test_langchain_cancellation_closes_owned_session() -> None:
    async def run() -> None:
        started = asyncio.Event()
        pending = asyncio.Event()
        resource = _FakeResponses(event_batches=[_text_events("resp_0", "ok")])
        client, http = _client(resource)
        model = _model(client)
        states: list[Any] = []

        async def consume() -> None:
            async with model.responses_session():
                states.append(client.current_responses_session())
                async with aclosing(model.astream("one")) as stream:
                    await anext(stream)
                    started.set()
                    await pending.wait()

        try:
            task = asyncio.create_task(consume())
            await asyncio.wait_for(started.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert resource.connections[0].closed
            assert states[0].last_response_id is None
            assert not states[0].in_flight
            assert client.current_responses_session() is None
        finally:
            await http.aclose()

    asyncio.run(run())


@pytest.mark.asyncio
async def test_langchain_payload_rejects_an_overlapping_websocket_request() -> None:
    client, http = _client(_FakeResponses())
    model = _model(client)
    try:
        payload = model._get_request_payload([HumanMessage("outside a session")])
        assert payload["input"]

        async with model.responses_session():
            state = client.current_responses_session()
            assert state is not None
            state.in_flight = True
            with pytest.raises(CodexResponsesProtocolError, match="one in-flight"):
                model._get_request_payload([HumanMessage("overlap")])
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_langchain_websocket_rejects_http_headers_and_mismatched_response_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, http = _client(_FakeResponses(event_batches=[_text_events("resp_one", "ok")]))
    try:
        headers_model = _model(client, include_response_headers=True)
        with pytest.raises(ValueError, match="headers are unavailable"):
            await headers_model.ainvoke("headers")

        original_converter = helper_langchain.message_chunk_to_message

        def mismatched_response_id(message: Any) -> Any:
            converted = original_converter(message)
            converted.response_metadata["id"] = "resp_other"
            return converted

        monkeypatch.setattr(helper_langchain, "message_chunk_to_message", mismatched_response_id)
        model = _model(client)
        async with model.responses_session():
            await model.ainvoke("mismatch")
            state = client.current_responses_session()
            assert state is not None
            assert state.last_response_id is None
            assert state.history_snapshot is None
    finally:
        await http.aclose()


def test_langchain_http_sync_generation_delegates_to_base_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, http = _client(_FakeResponses())
    model = _model(client)
    model.responses_connection = "http"
    expected = ChatGenerationChunk(message=AIMessageChunk(content="sync result"))

    def base_stream(*args: Any, **kwargs: Any):
        del args, kwargs
        yield expected

    monkeypatch.setattr(helper_langchain.ChatOpenAI, "_stream", base_stream)
    try:
        result = model._generate([HumanMessage("sync")])
        assert result.generations[0].text == "sync result"
    finally:
        asyncio.run(http.aclose())


def test_langchain_system_instruction_blocks_require_text() -> None:
    client, http = _client(_FakeResponses())
    model = _model(client)
    try:
        message = SystemMessage(
            content=cast("Any", ["first", {"type": "text", "text": "second"}]),
        )
        assert model._full_payload([message])["instructions"] == "first\nsecond"

        invalid = SystemMessage(
            content=[{"type": "image_url", "image_url": "https://example.test/image.png"}]
        )
        with pytest.raises(ValueError, match="text only"):
            model._full_payload([invalid])
    finally:
        asyncio.run(http.aclose())


def test_langchain_handshake_failure_fallback_and_send_failure_are_not_replayed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        # HTTP fallback reuses the complete payload, never a speculative response suffix.
        resource = _FakeResponses(connection_error=OSError("offline"))
        client, http = _client(resource)
        model = _model(client, responses_fallback="http")

        class HttpStream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def __aiter__(self):
                async def events():
                    for event in _text_events("resp_http", "ok"):
                        yield event

                return events()

        async def create(**kwargs):
            resource.http_calls.append(kwargs)
            return HttpStream()

        monkeypatch.setattr(resource, "create", create)
        try:
            async with model.responses_session():
                await model.ainvoke("one")
                await model.ainvoke("two")
            assert len(resource.http_calls) == 2
            assert all("previous_response_id" not in call for call in resource.http_calls)
            assert all(call["stream"] for call in resource.http_calls)
        finally:
            await http.aclose()

        failed = _FakeResponses(send_error=OSError("unknown send outcome"))
        client, http = _client(failed)
        try:
            with pytest.raises(CodexResponsesProtocolError, match="not replayed"):
                async for _ in _model(client, responses_fallback="http").astream("one"):
                    pass
            assert failed.http_calls == []
            assert failed.connections[0].closed
        finally:
            await http.aclose()

    asyncio.run(run())
