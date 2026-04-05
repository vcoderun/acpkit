from __future__ import annotations as _annotations

import httpx
from pydantic_ai.models.openai import OpenAIResponsesModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

from .auth import CodexAuthConfig
from .client import create_codex_async_openai
from .model import CodexResponsesModel

__all__ = ("create_codex_responses_model",)


def create_codex_responses_model(
    model_name: str,
    *,
    config: CodexAuthConfig | None = None,
    http_client: httpx.AsyncClient | None = None,
    settings: OpenAIResponsesModelSettings | None = None,
) -> CodexResponsesModel:
    client = create_codex_async_openai(config=config, http_client=http_client)
    model_settings: OpenAIResponsesModelSettings = {"openai_store": False}
    if settings is not None:
        model_settings.update(settings)
        model_settings.setdefault("openai_store", False)
    return CodexResponsesModel(
        model_name,
        provider=OpenAIProvider(openai_client=client),
        settings=model_settings,
    )
