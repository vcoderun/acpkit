from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from contextlib import aclosing, asynccontextmanager
from contextvars import ContextVar
from copy import deepcopy
from typing import Any, ClassVar, cast

from langchain_core.language_models.chat_models import agenerate_from_stream, generate_from_stream
from langchain_core.messages import BaseMessage, SystemMessage, message_chunk_to_message
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models.base import BaseChatOpenAI

from ._responses_websocket import (
    CodexResponsesConnection,
    CodexResponsesFallback,
    CodexResponsesProtocolError,
    CodexResponsesSessionInfo,
)
from .client import CodexAsyncOpenAI

_STREAMS: ContextVar[tuple[int, list[Any]] | None] = ContextVar(
    "codex_langchain_streams", default=None
)


class CodexChatOpenAI(ChatOpenAI):
    """LangChain serialization with Codex instructions and session-owned continuation."""

    supports_responses_session_isolation: ClassVar[bool] = True
    responses_connection: CodexResponsesConnection = "http"
    responses_fallback: CodexResponsesFallback = "error"

    @property
    def responses_session_owner(self) -> CodexAsyncOpenAI:
        return self.root_async_client

    @asynccontextmanager
    async def responses_session(
        self, *, isolate: bool = False
    ) -> AsyncIterator[CodexResponsesSessionInfo]:
        async with self.responses_session_owner.responses_session(
            connection=self.responses_connection,
            fallback=self.responses_fallback,
            isolate=isolate,
        ) as info:
            yield info

    @asynccontextmanager
    async def responses_turn(self) -> AsyncIterator[None]:
        """Keep routing state for one native graph run, including tool continuations."""
        async with self.responses_session(), self.responses_session_owner.responses_turn():
            yield

    def _full_payload(self, input_: Any, *, stop: Any = None, **kwargs: Any) -> dict[str, Any]:
        if self.use_previous_response_id or kwargs.get("previous_response_id"):
            raise ValueError(
                "Codex continuation is owned by responses_session(), not response IDs."
            )
        messages = self._convert_input(input_).to_messages()
        systems = [message for message in messages if isinstance(message, SystemMessage)]
        payload = super()._get_request_payload(
            [message for message in messages if not isinstance(message, SystemMessage)],
            stop=stop,
            **kwargs,
        )
        if systems:
            # Active system instructions replace the factory's fallback instructions.
            payload["instructions"] = "\n\n".join(_system_text(message) for message in systems)
        payload["store"] = False
        return payload

    def _get_request_payload(
        self, input_: Any, *, stop: Any = None, **kwargs: Any
    ) -> dict[str, Any]:
        payload = self._full_payload(input_, stop=stop, **kwargs)
        state = self.responses_session_owner.current_responses_session()
        if state is None or state.info.requested_connection != "websocket":
            return payload
        if state.info.effective_connection == "http":
            return payload
        if state.in_flight:
            raise CodexResponsesProtocolError(
                "A Responses WebSocket session supports one in-flight response at a time."
            )
        reset_reason = self.responses_session_owner._responses_connection_reset_reason(payload)
        if reset_reason is not None:
            state.reset_chain()
        contract = _contract(payload)
        history = payload["input"]
        acknowledged = state.history_snapshot
        if (
            state.last_response_id
            and acknowledged is not None
            and contract == state.request_contract
            and tuple(history[: len(acknowledged)]) == acknowledged
        ):
            payload["previous_response_id"] = state.last_response_id
            payload["input"] = history[len(acknowledged) :]
            state.continuation_reason = "acknowledged_prefix"
            state.acknowledged_message_count = len(acknowledged)
        else:
            reason = reset_reason or (
                "request_contract_changed"
                if state.request_contract is not None and state.request_contract != contract
                else "history_changed"
                if acknowledged is not None
                else "full_request"
            )
            state.reset_chain()
            state.continuation_reason = reason
            state.acknowledged_message_count = 0
        return payload

    async def astream(
        self, input: Any, config: Any = None, *, stop: Any = None, **kwargs: Any
    ) -> AsyncGenerator[Any, None]:
        # LangChain's public generator does not close its nested model generator on aclose.
        streams: list[Any] = []
        token = _STREAMS.set((id(asyncio.current_task()), streams))
        try:
            async with aclosing(
                cast(
                    AsyncGenerator[Any, None],
                    super().astream(input, config=config, stop=stop, **kwargs),
                )
            ) as stream:
                async for chunk in stream:
                    yield chunk
        finally:
            try:
                for stream in reversed(streams):
                    await stream.aclose()
            finally:
                _STREAMS.reset(token)

    def _astream(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> AsyncIterator[ChatGenerationChunk]:
        stream = self._stream_response(messages, stop=stop, run_manager=run_manager, **kwargs)
        scope = _STREAMS.get()
        if scope is not None and scope[0] == id(asyncio.current_task()):
            scope[1].append(stream)
        return stream

    async def _stream_response(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> AsyncIterator[ChatGenerationChunk]:
        async with self.responses_session():
            state = self.responses_session_owner.current_responses_session()
            if state is not None and state.in_flight:
                raise CodexResponsesProtocolError(
                    "A Responses WebSocket session supports one in-flight response at a time."
                )
            if self.responses_connection == "websocket" and self.include_response_headers:
                raise ValueError("Response HTTP headers are unavailable over Codex WebSocket.")
            full = self._full_payload(messages, stop=stop, **{**kwargs, "stream": True})
            history = deepcopy(tuple(full["input"]))
            contract = _contract(full)
            complete = False
            accumulated: ChatGenerationChunk | None = None
            try:
                async with aclosing(
                    cast(
                        AsyncGenerator[ChatGenerationChunk, None],
                        BaseChatOpenAI._astream_responses(
                            self, messages, stop=stop, run_manager=run_manager, **kwargs
                        ),
                    )
                ) as stream:
                    async for chunk in stream:
                        accumulated = chunk if accumulated is None else accumulated + chunk
                        yield chunk
                if state is not None and state.last_response_id and accumulated is not None:
                    message = message_chunk_to_message(accumulated.message)
                    if message.response_metadata.get("id") == state.last_response_id:
                        output = self._full_payload([message], **kwargs)["input"]
                        state.history_snapshot = (*history, *deepcopy(output))
                        state.request_contract = contract
                    else:
                        state.reset_chain()
                complete = True
            finally:
                if not complete and state is not None:
                    state.reset_chain()

    async def _agenerate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        return await agenerate_from_stream(
            self._astream(messages, stop=stop, run_manager=run_manager, **kwargs)
        )

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        if self.responses_connection == "websocket":
            raise ValueError(
                "Codex WebSocket requires ainvoke()/astream(), or a Kedi sync wrapper."
            )
        yield from super()._stream(*args, **kwargs)

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        return generate_from_stream(
            self._stream(messages, stop=stop, run_manager=run_manager, **kwargs)
        )


def _system_text(message: SystemMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    chunks: list[str] = []
    for block in message.content:
        if isinstance(block, str):
            chunks.append(block)
        elif block.get("type") == "text" and isinstance(block.get("text"), str):
            chunks.append(block["text"])
        else:
            raise ValueError("Codex system instructions must contain text only.")
    return "\n".join(chunks)


def _contract(payload: dict[str, Any]) -> str:
    return json.dumps(
        {key: value for key, value in payload.items() if key not in {"input", "stream"}},
        sort_keys=True,
        default=str,
    )
