from __future__ import annotations as _annotations

import asyncio

import httpx
from openai import AsyncOpenAI, Omit, OpenAI
from typing_extensions import override

from .auth import CodexAuthConfig, CodexAuthStore, CodexTokenManager

__all__ = (
    "CodexAsyncOpenAI",
    "CodexOpenAI",
    "create_codex_async_openai",
    "create_codex_openai",
)


def _resolve_default_headers(
    token_manager: CodexTokenManager, headers: dict[str, str | Omit]
) -> None:
    account_id = token_manager.current_account_id
    if account_id is not None:
        headers["ChatGPT-Account-Id"] = account_id
    headers.setdefault("originator", "codex-auth-helper")


class CodexAsyncOpenAI(AsyncOpenAI):
    def __init__(
        self,
        *,
        base_url: str,
        http_client: httpx.AsyncClient,
        token_manager: CodexTokenManager,
        owns_http_client: bool,
    ) -> None:
        self.token_manager = token_manager
        self._owns_http_client = owns_http_client
        super().__init__(
            api_key=token_manager.get_access_token,
            base_url=base_url,
            http_client=http_client,
        )

    @property
    @override
    def default_headers(self) -> dict[str, str | Omit]:
        headers = dict(super().default_headers)
        _resolve_default_headers(self.token_manager, headers)
        return headers

    @override
    async def close(self) -> None:
        if self._owns_http_client:
            await super().close()


class CodexOpenAI(OpenAI):
    def __init__(
        self,
        *,
        base_url: str,
        http_client: httpx.Client,
        token_manager: CodexTokenManager,
        owns_http_client: bool,
    ) -> None:
        self.token_manager = token_manager
        self._owns_http_client = owns_http_client
        super().__init__(
            api_key=token_manager.get_access_token_sync,
            base_url=base_url,
            http_client=http_client,
        )

    @property
    @override
    def default_headers(self) -> dict[str, str | Omit]:
        headers = dict(super().default_headers)
        _resolve_default_headers(self.token_manager, headers)
        return headers

    @override
    def close(self) -> None:
        if self._owns_http_client:
            super().close()
        if self.token_manager.owns_http_client:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(self.token_manager.close())
            else:
                loop.create_task(self.token_manager.close())


def create_codex_async_openai(
    *,
    config: CodexAuthConfig | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> CodexAsyncOpenAI:
    resolved_config = config or CodexAuthConfig()
    owns_http_client = http_client is None
    base_http_client = http_client or httpx.AsyncClient(
        follow_redirects=True,
        timeout=resolved_config.timeout_seconds,
    )
    token_manager = CodexTokenManager(
        config=resolved_config,
        store=CodexAuthStore(resolved_config.auth_path),
        http_client=base_http_client,
        owns_http_client=owns_http_client,
    )
    return CodexAsyncOpenAI(
        base_url=resolved_config.api_base_url,
        http_client=base_http_client,
        token_manager=token_manager,
        owns_http_client=owns_http_client,
    )


def create_codex_openai(
    *,
    config: CodexAuthConfig | None = None,
    http_client: httpx.Client | None = None,
) -> CodexOpenAI:
    resolved_config = config or CodexAuthConfig()
    owns_http_client = http_client is None
    base_http_client = http_client or httpx.Client(
        follow_redirects=True,
        timeout=resolved_config.timeout_seconds,
    )
    token_manager = CodexTokenManager(
        config=resolved_config,
        store=CodexAuthStore(resolved_config.auth_path),
        http_client=httpx.AsyncClient(
            follow_redirects=True,
            timeout=resolved_config.timeout_seconds,
        ),
        owns_http_client=True,
    )
    return CodexOpenAI(
        base_url=resolved_config.api_base_url,
        http_client=base_http_client,
        token_manager=token_manager,
        owns_http_client=owns_http_client,
    )
