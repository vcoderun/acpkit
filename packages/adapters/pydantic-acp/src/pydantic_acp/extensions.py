from __future__ import annotations as _annotations

from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeAlias

from acp.interfaces import Client as AcpClient
from acp.schema import (
    AuthenticateResponse,
    AuthMethodAgent,
    ClientCapabilities,
    EnvVarAuthMethod,
    Implementation,
    TerminalAuthMethod,
)

from .session.state import JsonValue

AuthenticationMethod: TypeAlias = EnvVarAuthMethod | TerminalAuthMethod | AuthMethodAgent

__all__ = (
    "AuthenticationMethod",
    "AuthenticationProvider",
    "ContextualExtensionRouter",
    "ExtensionContext",
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


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtensionContext:
    """Public state negotiated for one ACP client connection."""

    client: AcpClient
    protocol_version: int
    client_capabilities: ClientCapabilities | None
    client_info: Implementation | None


class ContextualExtensionRouter(Protocol):
    """Handle extension traffic with public connection-scoped ACP state."""

    def handle_method(
        self,
        context: ExtensionContext,
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue] | Awaitable[dict[str, JsonValue]]: ...

    def handle_notification(
        self,
        context: ExtensionContext,
        method: str,
        params: dict[str, JsonValue],
    ) -> None | Awaitable[None]: ...


def _validate_auth_methods(
    methods: Sequence[AuthenticationMethod],
) -> tuple[AuthenticationMethod, ...]:
    validated = tuple(methods)
    method_ids: set[str] = set()
    for method in validated:
        if not method.id.strip():
            raise ValueError("authentication method IDs must not be blank")
        if method.id in method_ids:
            raise ValueError(f"duplicate authentication method ID: {method.id!r}")
        method_ids.add(method.id)
    return validated


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
