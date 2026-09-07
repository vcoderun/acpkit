from __future__ import annotations as _annotations

from typing import TYPE_CHECKING, Any, Literal

import httpx
from pydantic_ai.models.openai import OpenAIResponsesModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

from ._responses_lite import CodexResponsesTransport
from ._responses_websocket import (
    CodexResponsesConnection,
    CodexResponsesFallback,
    CodexResponsesTransportObserver,
    validate_responses_connection,
)
from .auth import CodexAuthConfig
from .client import create_codex_async_openai, create_codex_openai
from .model import CodexResponsesModel

if TYPE_CHECKING:
    from .langchain import CodexChatOpenAI

__all__ = ("create_codex_chat_openai", "create_codex_responses_model")


def create_codex_responses_model(
    model_name: str,
    *,
    config: CodexAuthConfig | None = None,
    http_client: Any | None = None,
    auth_http_client: httpx.AsyncClient | None = None,
    instructions: str,
    settings: OpenAIResponsesModelSettings | None = None,
    transport: CodexResponsesTransport = "responses",
    connection: CodexResponsesConnection = "http",
    fallback: CodexResponsesFallback = "error",
    transport_observer: CodexResponsesTransportObserver | None = None,
) -> CodexResponsesModel:
    if instructions is None:
        raise ValueError(
            "`instructions` is required for Codex-backed Pydantic models. "
            "Pass an explicit system instruction string.",
        )
    if transport == "responses_lite" and connection == "websocket":
        raise ValueError("`responses_lite` does not support the WebSocket connection.")
    client = create_codex_async_openai(
        config=config,
        http_client=http_client,
        auth_http_client=auth_http_client,
        transport=transport,
        transport_observer=transport_observer,
    )
    model_settings: OpenAIResponsesModelSettings = {"openai_store": False}
    if settings is not None:
        model_settings.update(settings)
    model_settings["openai_store"] = False
    return CodexResponsesModel(
        model_name,
        default_instructions=instructions,
        provider=OpenAIProvider(openai_client=client),
        settings=model_settings,
        connection=connection,
        fallback=fallback,
    )


def create_codex_chat_openai(
    model_name: str,
    *,
    config: CodexAuthConfig | None = None,
    http_client: Any | None = None,
    auth_http_client: httpx.AsyncClient | None = None,
    instructions: str,
    sync_http_client: Any | None = None,
    sync_auth_http_client: httpx.AsyncClient | None = None,
    include_response_headers: bool = False,
    streaming: bool = True,
    model_kwargs: dict[str, Any] | None = None,
    output_version: Literal["v0", "responses/v1"] = "responses/v1",
    reasoning: dict[str, Any] | None = None,
    temperature: float | None = None,
    transport: CodexResponsesTransport = "responses",
    use_previous_response_id: bool = False,
    connection: CodexResponsesConnection = "http",
    fallback: CodexResponsesFallback = "error",
    transport_observer: CodexResponsesTransportObserver | None = None,
) -> CodexChatOpenAI:
    """Build a Codex-backed LangChain chat model via `langchain-openai`.

    The returned model is pinned to the OpenAI Responses API and reuses the
    same Codex auth flow as the Pydantic helper path.
    """
    try:
        from .langchain import CodexChatOpenAI
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Install the optional LangChain dependency first: "
            'uv add "codex-auth-helper[langchain]" or '
            'pip install "codex-auth-helper[langchain]".',
        ) from exc

    validate_responses_connection(connection, fallback)
    if transport == "responses_lite" and connection == "websocket":
        raise ValueError("`responses_lite` does not support the WebSocket connection.")
    if use_previous_response_id:
        raise ValueError("Codex continuation is owned by responses_session(), not response IDs.")
    if connection == "websocket" and include_response_headers:
        raise ValueError("Response HTTP headers are unavailable over Codex WebSocket.")
    async_root_client = create_codex_async_openai(
        config=config,
        http_client=http_client,
        auth_http_client=auth_http_client,
        transport=transport,
        transport_observer=transport_observer,
    )
    sync_root_client = create_codex_openai(
        config=config,
        http_client=sync_http_client,
        auth_http_client=sync_auth_http_client,
        transport=transport,
    )
    chat_model_kwargs = dict(model_kwargs or {})
    if instructions is None:
        raise ValueError(
            "`instructions` is required for Codex-backed LangChain models. "
            "Pass an explicit system instruction string.",
        )
    if "store" in chat_model_kwargs:
        raise ValueError(
            "Do not pass `model_kwargs['store']`; Codex-backed ChatOpenAI always forces "
            "`store=False`.",
        )
    if "instructions" in chat_model_kwargs:
        raise ValueError(
            "Pass `instructions` either through the dedicated parameter or "
            "`model_kwargs['instructions']`, not both.",
        )
    chat_model_kwargs["instructions"] = instructions
    chat_openai_kwargs: dict[str, Any] = {
        "model": model_name,
        "async_client": async_root_client.chat.completions,
        "client": sync_root_client.chat.completions,
        "include_response_headers": include_response_headers,
        "model_kwargs": chat_model_kwargs,
        "output_version": output_version,
        "reasoning": reasoning,
        "root_async_client": async_root_client,
        "root_client": sync_root_client,
        "store": False,
        "temperature": temperature,
        "use_previous_response_id": use_previous_response_id,
        "use_responses_api": True,
        "streaming": streaming,
        "responses_connection": connection,
        "responses_fallback": fallback,
    }
    return CodexChatOpenAI(
        **chat_openai_kwargs,
    )
