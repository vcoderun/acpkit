from __future__ import annotations as _annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager, nullcontext
from copy import deepcopy
from dataclasses import fields, replace
from typing import Any, cast

from pydantic_ai.messages import InstructionPart, ModelMessage, ModelRequest, ModelResponse
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import RunContext
from typing_extensions import override

from ._headers import codex_request_headers
from ._responses_websocket import (
    CodexResponsesConnection,
    CodexResponsesFallback,
    CodexResponsesProtocolError,
    CodexResponsesSessionInfo,
    validate_responses_connection,
)
from .client import CodexAsyncOpenAI

__all__ = ("CodexResponsesModel",)


class CodexResponsesModel(OpenAIResponsesModel):
    supports_responses_session_isolation = True

    def __init__(
        self,
        model_name: str,
        *,
        default_instructions: str,
        provider: Any = "openai",
        profile: Any = None,
        settings: ModelSettings | None = None,
        connection: CodexResponsesConnection = "http",
        fallback: CodexResponsesFallback = "error",
    ) -> None:
        validate_responses_connection(connection, fallback)
        self._default_instructions = default_instructions
        self.responses_connection: CodexResponsesConnection = connection
        self.responses_fallback: CodexResponsesFallback = fallback
        super().__init__(
            model_name,
            provider=provider,
            profile=profile,
            settings=settings,
        )

    @asynccontextmanager
    async def responses_session(
        self,
        *,
        isolate: bool = False,
    ) -> AsyncIterator[CodexResponsesSessionInfo]:
        client = self.client
        if not isinstance(client, CodexAsyncOpenAI):
            raise TypeError("CodexResponsesModel requires a CodexAsyncOpenAI client.")
        async with client.responses_session(
            connection=self.responses_connection,
            fallback=self.responses_fallback,
            isolate=isolate,
        ) as info:
            yield info

    @property
    def responses_session_owner(self) -> Any:
        return self.client

    @asynccontextmanager
    async def responses_turn(self) -> AsyncIterator[None]:
        """Keep routing state for one native agent run, including tool continuations."""
        client = self.client
        if not isinstance(client, CodexAsyncOpenAI):
            raise TypeError("CodexResponsesModel requires a CodexAsyncOpenAI client.")
        async with self.responses_session(), client.responses_turn():
            yield

    @override
    async def _responses_create(
        self,
        messages: list[ModelRequest | ModelResponse],
        stream: bool,
        model_settings: OpenAIResponsesModelSettings,
        model_request_parameters: ModelRequestParameters,
    ) -> Any:
        client = self.client
        state = client.current_responses_session() if isinstance(client, CodexAsyncOpenAI) else None
        settings = model_settings.copy()
        if state is not None and state.info.requested_connection == "websocket":
            settings.pop("openai_previous_response_id", None)
        settings["extra_headers"] = codex_request_headers(model_settings.get("extra_headers"))
        websocket_active = (
            state is not None
            and state.info.requested_connection == "websocket"
            and state.info.effective_connection != "http"
        )
        history_snapshot = None
        request_contract = None
        if websocket_active and state is not None:
            if state.in_flight:
                raise CodexResponsesProtocolError(
                    "A Responses WebSocket session supports one in-flight response at a time."
                )
            extra_headers, _ = self._build_request_options(settings)
            if (
                cast(CodexAsyncOpenAI, client)._responses_connection_reset_reason(
                    {
                        "extra_headers": extra_headers,
                        "model": self.model_name,
                        "service_tier": settings.get("openai_service_tier"),
                        "extra_body": settings.get("extra_body"),
                    }
                )
                is not None
            ):
                state.reset_chain()
            instruction_parts = self._get_instruction_parts(
                messages,
                model_request_parameters,
            )
            if model_request_parameters.instruction_parts is None:
                model_request_parameters = replace(
                    model_request_parameters,
                    instruction_parts=instruction_parts
                    or [InstructionPart(content=self._default_instructions)],
                )
            request_contract = _request_contract(settings, model_request_parameters)
            history_snapshot = _history_snapshot(messages)
            if (
                state.last_response_id is not None
                and state.history_snapshot is not None
                and history_snapshot is not None
                and len(history_snapshot) >= len(state.history_snapshot)
                and history_snapshot[: len(state.history_snapshot)] == state.history_snapshot
                and state.request_contract == request_contract
            ):
                settings["openai_previous_response_id"] = "auto"
                state.continuation_reason = "append_only"
                state.acknowledged_message_count = len(state.history_snapshot)
            else:
                if state.last_response_id is None:
                    continuation_reason = "no_completed_response"
                elif state.request_contract != request_contract:
                    continuation_reason = "request_contract_changed"
                else:
                    continuation_reason = "history_changed"
                settings.pop("openai_previous_response_id", None)
                state.reset_chain()
                state.continuation_reason = continuation_reason
                state.acknowledged_message_count = 0

        create_response = cast(Any, super()._responses_create)
        response = await create_response(
            messages,
            stream,
            settings,
            model_request_parameters,
        )
        if websocket_active and state is not None:
            state.history_snapshot = history_snapshot
            state.request_contract = request_contract
        return response

    def _with_default_instructions(
        self,
        messages: Sequence[ModelMessage],
        model_request_parameters: ModelRequestParameters,
    ) -> tuple[list[ModelMessage], ModelRequestParameters]:
        resolved = super()._get_instruction_parts(messages, model_request_parameters)
        if resolved:
            return list(messages), model_request_parameters
        return list(messages), replace(
            model_request_parameters,
            instruction_parts=[InstructionPart(content=self._default_instructions)],
        )

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        prepared_messages, prepared_parameters = self._with_default_instructions(
            messages, model_request_parameters
        )
        scope = (
            self.responses_session() if self.responses_connection == "websocket" else nullcontext()
        )
        async with scope:
            retries = 0
            while True:
                try:
                    async with self.request_stream(
                        prepared_messages,
                        model_settings,
                        prepared_parameters,
                    ) as streamed_response:
                        async for _ in streamed_response:
                            pass
                        return streamed_response.get()
                except Exception as exc:
                    client = self.client
                    if not isinstance(
                        client, CodexAsyncOpenAI
                    ) or not await client.recover_response(exc, retries=retries):
                        raise
                    retries += 1

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        prepared_messages, prepared_parameters = self._with_default_instructions(
            messages, model_request_parameters
        )
        client = self.client
        has_outer_session = (
            isinstance(client, CodexAsyncOpenAI) and client.current_responses_session() is not None
        )
        if self.responses_connection == "websocket" and not has_outer_session:
            async with (
                self.responses_session(),
                super().request_stream(
                    prepared_messages,
                    model_settings,
                    prepared_parameters,
                    run_context=run_context,
                ) as streamed_response,
            ):
                yield streamed_response
            return
        async with super().request_stream(
            prepared_messages,
            model_settings,
            prepared_parameters,
            run_context=run_context,
        ) as streamed_response:
            yield streamed_response
        if isinstance(client, CodexAsyncOpenAI):
            state = client.current_responses_session()
            response = streamed_response.get()
            if (
                state is not None
                and state.last_response_id == response.provider_response_id
                and state.history_snapshot is not None
            ):
                output_snapshot = _history_snapshot([response])
                state.history_snapshot = (
                    (*state.history_snapshot, *output_snapshot)
                    if output_snapshot is not None
                    else None
                )


def _history_snapshot(
    messages: Sequence[ModelRequest | ModelResponse],
) -> tuple[Any, ...] | None:
    # Snapshot request-relevant values, excluding run IDs and usage added later.
    try:
        return tuple(
            (
                type(message),
                deepcopy(message.parts),
                message.instructions
                if isinstance(message, ModelRequest)
                else (
                    message.model_name,
                    message.provider_name,
                    message.provider_response_id,
                    deepcopy(message.provider_details),
                ),
            )
            for message in messages
        )
    except Exception:
        # Opaque tool values may not be copyable. Full history remains valid.
        return None


def _request_contract(
    model_settings: Mapping[str, Any],
    model_request_parameters: ModelRequestParameters,
) -> str:
    settings = {
        key: value
        for key, value in model_settings.items()
        if key not in {"openai_previous_response_id", "openai_conversation_id"}
    }
    parameters = tuple(
        (field.name, repr(getattr(model_request_parameters, field.name)))
        for field in fields(model_request_parameters)
    )
    return repr((sorted(settings.items()), parameters))
