from __future__ import annotations as _annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from codex_auth_helper import (
    CodexAuthConfig,
    CodexResponsesModel,
    create_codex_chat_openai,
    create_codex_responses_model,
)
from openai import AsyncOpenAI
from pydantic_ai.messages import (
    InstructionPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import RunContext

from .support import write_auth_file
from .test_websocket import _text_events


@pytest.mark.parametrize(
    ("history_instruction", "explicit_instruction", "expected"),
    [
        (None, None, "Factory default."),
        ("History instruction.", None, "History instruction."),
        (None, "Request instruction.", "Request instruction."),
        ("History instruction.", "Request instruction.", "Request instruction."),
    ],
)
async def test_instruction_preparation_preserves_history_and_request_parameters(
    tmp_path: Path,
    history_instruction: str | None,
    explicit_instruction: str | None,
    expected: str,
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path)
    model = create_codex_responses_model(
        "gpt-test", config=CodexAuthConfig(auth_path=auth_path), instructions="Factory default."
    )
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart("Keep this message.")], instructions=history_instruction)
    ]
    parameters = ModelRequestParameters(
        instruction_parts=[InstructionPart(content=explicit_instruction)]
        if explicit_instruction is not None
        else None
    )
    original = deepcopy((messages, parameters))
    try:
        prepared, prepared_parameters = model._with_default_instructions(messages, parameters)
        assert prepared == messages
        assert prepared is not messages
        assert prepared[0] is messages[0]
        assert (messages, parameters) == original
        instructions = model._get_instruction_parts(prepared, prepared_parameters)
        assert instructions is not None
        assert [part.content for part in instructions] == [expected]
        if history_instruction is not None or explicit_instruction is not None:
            assert prepared_parameters is parameters
        else:
            assert prepared_parameters is not parameters
        again, again_parameters = model._with_default_instructions(prepared, prepared_parameters)
        assert again == messages
        assert again_parameters is prepared_parameters
    finally:
        await model.client.close()


@pytest.mark.parametrize("connection", ["http", "websocket"])
async def test_stream_forwards_run_context_without_inserting_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, connection: Any
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path)
    model = create_codex_responses_model(
        "gpt-test",
        config=CodexAuthConfig(auth_path=auth_path),
        instructions="Factory default.",
        connection=connection,
    )
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart("One message.")])]
    context = cast(RunContext[Any], object())
    parameters = ModelRequestParameters()
    calls: list[tuple[list[ModelMessage], ModelRequestParameters, RunContext[Any] | None]] = []

    class Result:
        def get(self) -> ModelResponse:
            return ModelResponse(parts=[TextPart("ok")])

    @asynccontextmanager
    async def stream(
        self: OpenAIResponsesModel,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        calls.append((messages, model_request_parameters, run_context))
        yield cast(StreamedResponse, Result())

    monkeypatch.setattr(OpenAIResponsesModel, "request_stream", stream)
    try:
        async with model.request_stream(messages, None, parameters, run_context=context):
            pass
        assert len(calls) == 1
        assert calls[0][0] == messages
        assert calls[0][2] is context
        assert calls[0][1].instruction_parts == [InstructionPart(content="Factory default.")]
        assert parameters.instruction_parts is None
    finally:
        await model.client.close()


class _PausedStream:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[Any]:
        events = _text_events("resp_stream", "hello ")
        yield events[0]
        yield events[1]
        await self.release.wait()
        yield events[1].model_copy(update={"delta": "world", "sequence_number": 2})
        yield events[2].model_copy(update={"sequence_number": 3})

    async def __aenter__(self) -> _PausedStream:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def close(self) -> None:
        self.closed = True


class _Responses:
    def __init__(self, stream: _PausedStream) -> None:
        self.stream = stream
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _PausedStream:
        self.calls.append(kwargs)
        return self.stream


class _StreamingClient:
    base_url = "https://example.test/v1/"
    api_key = "synthetic"

    def __init__(self, stream: _PausedStream) -> None:
        self.responses = _Responses(stream)


async def test_pydantic_text_arrives_before_stream_completion() -> None:
    stream = _PausedStream()
    client = _StreamingClient(stream)
    model = CodexResponsesModel(
        "gpt-test",
        default_instructions="Factory default.",
        provider=OpenAIProvider(openai_client=cast(AsyncOpenAI, client)),
    )
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart("stream")])]
    try:
        async with asyncio.timeout(2):
            async with model.request_stream(messages, None, ModelRequestParameters()) as response:
                iterator = aiter(response)
                first = await anext(iterator)
                assert isinstance(first, PartStartEvent)
                assert isinstance(first.part, TextPart)
                assert first.part.content == "hello "
                assert not stream.release.is_set()
                stream.release.set()
                remaining = [event async for event in iterator]
                assert any(
                    isinstance(event, PartDeltaEvent)
                    and isinstance(event.delta, TextPartDelta)
                    and event.delta.content_delta == "world"
                    for event in remaining
                )
                final_part = response.get().parts[0]
                assert isinstance(final_part, TextPart)
                assert final_part.content == "hello world"
    finally:
        stream.release.set()
    assert stream.closed
    assert client.responses.calls[0]["stream"] is True
    assert client.responses.calls[0]["instructions"] == "Factory default."
    assert client.responses.calls[0]["input"] == [{"role": "user", "content": "stream"}]
    assert len(messages) == 1


@pytest.mark.parametrize("streaming", [False, True])
async def test_langchain_factory_exposes_streaming_without_changing_transport(
    tmp_path: Path, streaming: bool
) -> None:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path)
    model = create_codex_chat_openai(
        "gpt-test",
        config=CodexAuthConfig(auth_path=auth_path),
        instructions="Factory default.",
        streaming=streaming,
    )
    try:
        assert model.streaming is streaming
        assert model.store is False
        assert model.use_responses_api is True
        assert model.responses_connection == "http"
    finally:
        await asyncio.to_thread(model.root_client.close)
        await model.root_async_client.close()
