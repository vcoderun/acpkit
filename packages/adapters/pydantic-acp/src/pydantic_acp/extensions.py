from __future__ import annotations as _annotations

from collections.abc import Awaitable, Sequence
from typing import Protocol, TypeAlias

from acp.schema import (
    AuthenticateResponse,
    AuthMethodAgent,
    ClientCapabilities,
    EnvVarAuthMethod,
    TerminalAuthMethod,
)

from .session.state import JsonValue

AuthenticationMethod: TypeAlias = EnvVarAuthMethod | TerminalAuthMethod | AuthMethodAgent

__all__ = (
    "AuthenticationMethod",
    "AuthenticationProvider",
    "ExtensionRouter",
)


class AuthenticationProvider(Protocol):
    """Provide ACP authentication methods and execute authentication requests."""

    def get_auth_methods(
        self,
        client_capabilities: ClientCapabilities | None,
    ) -> Sequence[AuthenticationMethod] | Awaitable[Sequence[AuthenticationMethod]]: ...

    def authenticate(
        self,
        method_id: str,
    ) -> AuthenticateResponse | None | Awaitable[AuthenticateResponse | None]: ...


class ExtensionRouter(Protocol):
    """Handle application-owned ACP extension methods and notifications."""

    async def handle_method(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]: ...

    async def handle_notification(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> None: ...


def _filter_auth_methods_for_client(
    methods: Sequence[AuthenticationMethod],
    client_capabilities: ClientCapabilities | None,
) -> list[AuthenticationMethod]:
    supports_terminal_auth = (
        client_capabilities is not None
        and client_capabilities.auth is not None
        and client_capabilities.auth.terminal is True
    )
    if supports_terminal_auth:
        return list(methods)
    return [method for method in methods if not isinstance(method, TerminalAuthMethod)]
