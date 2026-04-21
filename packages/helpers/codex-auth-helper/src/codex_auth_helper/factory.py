from __future__ import annotations as _annotations

from typing import TYPE_CHECKING, Any, Literal

import httpx
from pydantic_ai.models.openai import OpenAIResponsesModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

from .auth import CodexAuthConfig
from .client import create_codex_async_openai, create_codex_openai
from .model import CodexResponsesModel

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI

__all__ = ("create_codex_chat_openai", "create_codex_responses_model")


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


def create_codex_chat_openai(
    model_name: str,
    *,
    config: CodexAuthConfig | None = None,
    http_client: httpx.AsyncClient | None = None,
    sync_http_client: httpx.Client | None = None,
    include_response_headers: bool = False,
    model_kwargs: dict[str, Any] | None = None,
    output_version: Literal["v0", "responses/v1"] = "responses/v1",
    reasoning: dict[str, Any] | None = None,
    temperature: float | None = None,
    use_previous_response_id: bool = False,
) -> ChatOpenAI:
    """Build a Codex-backed LangChain chat model via `langchain-openai`.

    The returned model is pinned to the OpenAI Responses API and reuses the
    same Codex auth flow as the Pydantic helper path.
    """

    try:
        from langchain_openai import ChatOpenAI
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Install the optional LangChain dependency first: "
            'uv add "codex-auth-helper[langchain]" or '
            'pip install "codex-auth-helper[langchain]".'
        ) from exc

    async_root_client = create_codex_async_openai(config=config, http_client=http_client)
    sync_root_client = create_codex_openai(config=config, http_client=sync_http_client)
    chat_model_kwargs = dict(model_kwargs or {})
    return ChatOpenAI(
        model_name=model_name,
        async_client=async_root_client.chat.completions,
        client=sync_root_client.chat.completions,
        include_response_headers=include_response_headers,
        model_kwargs=chat_model_kwargs,
        output_version=output_version,
        reasoning=reasoning,
        root_async_client=async_root_client,
        root_client=sync_root_client,
        temperature=temperature,
        use_previous_response_id=use_previous_response_id,
        use_responses_api=True,
    )
