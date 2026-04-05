from __future__ import annotations as _annotations

from pydantic_ai.messages import ModelRequest, ModelResponse
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.settings import ModelSettings

__all__ = ("CodexResponsesModel",)


class CodexResponsesModel(OpenAIResponsesModel):
    async def request(
        self,
        messages: list[ModelRequest | ModelResponse],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        async with super().request_stream(
            messages,
            model_settings,
            model_request_parameters,
        ) as streamed_response:
            async for _ in streamed_response:
                pass
            return streamed_response.get()
