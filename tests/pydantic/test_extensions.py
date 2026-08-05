from __future__ import annotations as _annotations

import asyncio
from dataclasses import dataclass, field

import pytest
from acp.exceptions import RequestError
from acp.schema import (
    AuthCapabilities,
    AuthenticateResponse,
    AuthMethodAgent,
    ClientCapabilities,
    TerminalAuthMethod,
)
from pydantic_acp import AdapterConfig, AuthenticationMethod, JsonValue, create_acp_agent
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel


@dataclass(slots=True)
class _RecordingExtensionRouter:
    notifications: list[tuple[str, dict[str, JsonValue]]] = field(default_factory=list)

    async def handle_method(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if method == "demo.echo":
            return {"echo": params.get("value")}
        raise RequestError.invalid_params({"method": method})

    async def handle_notification(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> None:
        self.notifications.append((method, params))


@dataclass(slots=True)
class _RecordingAuthenticationProvider:
    capabilities: list[ClientCapabilities | None] = field(default_factory=list)
    authenticated_method_ids: list[str] = field(default_factory=list)

    def get_auth_methods(
        self,
        client_capabilities: ClientCapabilities | None,
    ) -> tuple[AuthenticationMethod, ...]:
        self.capabilities.append(client_capabilities)
        return (
            AuthMethodAgent(id="agent-login", name="Agent login"),
            TerminalAuthMethod(id="terminal-login", name="Terminal login", type="terminal"),
        )

    async def authenticate(self, method_id: str) -> AuthenticateResponse:
        if method_id != "agent-login":
            raise RequestError.invalid_params({"methodId": method_id})
        self.authenticated_method_ids.append(method_id)
        return AuthenticateResponse(field_meta={"methodId": method_id})


class _AsyncMethodsSyncAuthenticationProvider:
    async def get_auth_methods(
        self,
        client_capabilities: ClientCapabilities | None,
    ) -> tuple[AuthenticationMethod, ...]:
        del client_capabilities
        return (AuthMethodAgent(id="async-methods", name="Async methods"),)

    def authenticate(self, method_id: str) -> None:
        assert method_id == "async-methods"


def test_extension_router_handles_methods_notifications_and_structured_errors() -> None:
    router = _RecordingExtensionRouter()
    adapter = create_acp_agent(
        agent=Agent(TestModel()),
        config=AdapterConfig(extension_router=router),
    )

    result = asyncio.run(adapter.ext_method("demo.echo", {"value": [1, "two"]}))
    asyncio.run(adapter.ext_notification("demo.changed", {"revision": 3}))

    assert result == {"echo": [1, "two"]}
    assert router.notifications == [("demo.changed", {"revision": 3})]

    with pytest.raises(RequestError) as exc_info:
        asyncio.run(adapter.ext_method("demo.invalid", {}))

    assert exc_info.value.code == RequestError.invalid_params().code
    assert exc_info.value.data == {"method": "demo.invalid"}


def test_authentication_provider_advertises_supported_methods_and_authenticates() -> None:
    provider = _RecordingAuthenticationProvider()
    adapter = create_acp_agent(
        agent=Agent(TestModel()),
        config=AdapterConfig(authentication_provider=provider),
    )

    default_response = asyncio.run(adapter.initialize(protocol_version=1))
    terminal_response = asyncio.run(
        adapter.initialize(
            protocol_version=1,
            client_capabilities=ClientCapabilities(auth=AuthCapabilities(terminal=True)),
        ),
    )
    auth_response = asyncio.run(adapter.authenticate("agent-login"))

    assert default_response.auth_methods is not None
    assert [method.id for method in default_response.auth_methods] == ["agent-login"]
    assert terminal_response.auth_methods is not None
    assert [method.id for method in terminal_response.auth_methods] == [
        "agent-login",
        "terminal-login",
    ]
    assert provider.capabilities == [None, ClientCapabilities(auth=AuthCapabilities(terminal=True))]
    assert provider.authenticated_method_ids == ["agent-login"]
    assert auth_response is not None
    assert auth_response.field_meta == {"methodId": "agent-login"}

    with pytest.raises(RequestError) as exc_info:
        asyncio.run(adapter.authenticate("missing"))

    assert exc_info.value.code == RequestError.invalid_params().code
    assert exc_info.value.data == {"methodId": "missing"}


def test_authentication_provider_accepts_async_discovery_and_sync_authentication() -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel()),
        config=AdapterConfig(
            authentication_provider=_AsyncMethodsSyncAuthenticationProvider(),
        ),
    )

    response = asyncio.run(adapter.initialize(protocol_version=1))

    assert response.auth_methods is not None
    assert [method.id for method in response.auth_methods] == ["async-methods"]
    assert asyncio.run(adapter.authenticate("async-methods")) is None
