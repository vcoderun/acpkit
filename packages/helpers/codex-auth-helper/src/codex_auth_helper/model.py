from __future__ import annotations as _annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

from pydantic_ai.messages import InstructionPart, ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import RunContext

__all__ = ("CodexResponsesModel",)


class CodexResponsesModel(OpenAIResponsesModel):
    def __init__(
        self,
        model_name: str,
        *,
        default_instructions: str,
        provider: Any = "openai",
        profile: Any = None,
        settings: ModelSettings | None = None,
    ) -> None:
        self._default_instructions = default_instructions
        super().__init__(
            model_name,
            provider=provider,
            profile=profile,
            settings=settings,
        )

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
            messages,
            model_request_parameters,
        )
        async with self.request_stream(
            prepared_messages,
            model_settings,
            prepared_parameters,
        ) as streamed_response:
            async for _ in streamed_response:
                pass
            return streamed_response.get()

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        prepared_messages, prepared_parameters = self._with_default_instructions(
            messages,
            model_request_parameters,
        )
        async with super().request_stream(
            prepared_messages,
            model_settings,
            prepared_parameters,
            run_context=run_context,
        ) as streamed_response:
            yield streamed_response
