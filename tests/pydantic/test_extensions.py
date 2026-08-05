from __future__ import annotations as _annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from acp import connect_to_agent, run_agent
from acp.client.connection import ClientSideConnection
from acp.exceptions import RequestError
from acp.schema import (
    AuthCapabilities,
    AuthenticateResponse,
    AuthMethodAgent,
    ClientCapabilities,
    Implementation,
    TerminalAuthMethod,
)
from pydantic_acp import (
    AdapterConfig,
    AuthenticationMethod,
    ExtensionContext,
    JsonValue,
    RecordingACPClient,
    create_acp_agent,
)
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

_AUTH_METHODS: tuple[AuthenticationMethod, ...] = (
    AuthMethodAgent(id="agent-login", name="Agent login"),
    TerminalAuthMethod(id="terminal-login", name="Terminal login", type="terminal"),
)


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
    methods: tuple[AuthenticationMethod, ...] = _AUTH_METHODS
    capabilities: list[ClientCapabilities | None] = field(default_factory=list)
    authenticated_method_ids: list[str] = field(default_factory=list)

    def get_auth_methods(
        self,
        client_capabilities: ClientCapabilities | None,
    ) -> tuple[AuthenticationMethod, ...]:
        self.capabilities.append(client_capabilities)
        return self.methods

    async def authenticate(self, method_id: str) -> AuthenticateResponse:
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


@dataclass(slots=True)
class _RecordingContextualRouter:
    method_contexts: list[ExtensionContext] = field(default_factory=list)
    notification_contexts: list[ExtensionContext] = field(default_factory=list)

    def handle_method(
        self,
        context: ExtensionContext,
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        self.method_contexts.append(context)
        return {
            "client": context.client_info.name if context.client_info is not None else None,
            "method": method,
            "params": params,
            "protocolVersion": context.protocol_version,
            "terminal": context.client_capabilities.auth.terminal
            if context.client_capabilities is not None
            and context.client_capabilities.auth is not None
            else None,
        }

    async def handle_notification(
        self,
        context: ExtensionContext,
        method: str,
        params: dict[str, JsonValue],
    ) -> None:
        del method, params
        self.notification_contexts.append(context)


@dataclass(slots=True)
class _OpenAdapterConnection:
    connection: ClientSideConnection
    agent_task: asyncio.Task[None]
    client_writer: asyncio.StreamWriter
    agent_writer: asyncio.StreamWriter

    async def close(self) -> None:
        await self.connection.close()
        self.client_writer.close()
        self.agent_writer.close()
        await self.client_writer.wait_closed()
        await self.agent_writer.wait_closed()
        await self.agent_task


async def _open_adapter_connection(
    adapter: Any,
) -> _OpenAdapterConnection:
    loop = asyncio.get_running_loop()
    accepted: asyncio.Future[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = (
        loop.create_future()
    )

    async def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        accepted.set_result((reader, writer))

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    assert server.sockets is not None
    port = next(iter(server.sockets)).getsockname()[1]
    client_reader, client_writer = await asyncio.open_connection("127.0.0.1", port)
    agent_reader, agent_writer = await accepted
    server.close()
    await server.wait_closed()
    agent_task = asyncio.create_task(
        run_agent(
            adapter,
            input_stream=agent_writer,
            output_stream=agent_reader,
        ),
    )
    connection = connect_to_agent(RecordingACPClient(), client_writer, client_reader)
    return _OpenAdapterConnection(
        connection=connection,
        agent_task=agent_task,
        client_writer=client_writer,
        agent_writer=agent_writer,
    )


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


def test_adapter_config_rejects_ambiguous_extension_routers() -> None:
    legacy_router = _RecordingExtensionRouter()
    contextual_router = _RecordingContextualRouter()

    with pytest.raises(ValueError, match="either `extension_router`"):
        AdapterConfig(
            extension_router=legacy_router,
            contextual_extension_router=contextual_router,
        )


def test_contextual_extension_router_requires_initialized_connection() -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel()),
        config=AdapterConfig(contextual_extension_router=_RecordingContextualRouter()),
    )

    with pytest.raises(RequestError) as disconnected:
        asyncio.run(adapter.ext_method("demo.context", {}))
    assert disconnected.value.data == {"reason": "client_not_connected"}

    adapter.on_connect(cast("Any", object()))
    with pytest.raises(RequestError) as uninitialized:
        asyncio.run(adapter.ext_method("demo.context", {}))
    assert uninitialized.value.data == {"reason": "connection_not_initialized"}


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
    assert provider.authenticated_method_ids == ["agent-login"]


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


@pytest.mark.parametrize(
    ("methods", "message"),
    [
        (
            (AuthMethodAgent(id=" ", name="Blank"),),
            "must not be blank",
        ),
        (
            (
                AuthMethodAgent(id="duplicate", name="First"),
                AuthMethodAgent(id="duplicate", name="Second"),
            ),
            "duplicate authentication method ID",
        ),
    ],
)
def test_authentication_method_ids_fail_initialization_deterministically(
    methods: tuple[AuthenticationMethod, ...],
    message: str,
) -> None:
    provider = _RecordingAuthenticationProvider(methods=methods)
    adapter = create_acp_agent(
        agent=Agent(TestModel()),
        config=AdapterConfig(authentication_provider=provider),
    )

    with pytest.raises(ValueError, match=message):
        asyncio.run(adapter.initialize(protocol_version=1))
    assert provider.authenticated_method_ids == []


@pytest.mark.asyncio
async def test_connection_scoped_extensions_and_authentication_are_isolated() -> None:
    router = _RecordingContextualRouter()
    provider = _RecordingAuthenticationProvider()
    adapter = create_acp_agent(
        agent=Agent(TestModel()),
        config=AdapterConfig(
            authentication_provider=provider,
            contextual_extension_router=router,
        ),
    )
    first = await _open_adapter_connection(adapter)
    second = await _open_adapter_connection(adapter)
    try:
        first_response, second_response = await asyncio.gather(
            first.connection.initialize(
                protocol_version=1,
                client_capabilities=ClientCapabilities(
                    auth=AuthCapabilities(terminal=False),
                ),
                client_info=Implementation(name="first-client", version="1"),
            ),
            second.connection.initialize(
                protocol_version=1,
                client_capabilities=ClientCapabilities(
                    auth=AuthCapabilities(terminal=True),
                ),
                client_info=Implementation(name="second-client", version="1"),
            ),
        )
        assert first_response.auth_methods is not None
        assert [method.id for method in first_response.auth_methods] == ["agent-login"]
        assert second_response.auth_methods is not None
        assert [method.id for method in second_response.auth_methods] == [
            "agent-login",
            "terminal-login",
        ]

        with pytest.raises(RequestError) as filtered:
            await first.connection.authenticate(method_id="terminal-login")
        assert filtered.value.data == {"methodId": "terminal-login"}
        assert provider.authenticated_method_ids == []

        await second.connection.authenticate(method_id="terminal-login")
        method_results = await asyncio.gather(
            first.connection.ext_method(method="demo.context", params={"connection": 1}),
            second.connection.ext_method(method="demo.context", params={"connection": 2}),
        )
        await asyncio.gather(
            first.connection.ext_notification(method="demo.note", params={}),
            second.connection.ext_notification(method="demo.note", params={}),
        )
    finally:
        await asyncio.gather(first.close(), second.close())

    assert method_results == [
        {
            "client": "first-client",
            "method": "demo.context",
            "params": {"connection": 1},
            "protocolVersion": 1,
            "terminal": False,
        },
        {
            "client": "second-client",
            "method": "demo.context",
            "params": {"connection": 2},
            "protocolVersion": 1,
            "terminal": True,
        },
    ]
    assert provider.authenticated_method_ids == ["terminal-login"]
    assert len(router.method_contexts) == 2
    assert len(router.notification_contexts) == 2
    assert router.method_contexts[0].client is not router.method_contexts[1].client
