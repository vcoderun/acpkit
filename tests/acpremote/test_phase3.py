from __future__ import annotations as _annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from acp import connect_to_agent, run_agent, text_block
from acp.interfaces import Agent, Client
from acp.schema import (
    AgentMessageChunk,
    AuthenticateResponse,
    ClientCapabilities,
    CloseSessionResponse,
    FileSystemCapabilities,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    RequestPermissionResponse,
    SessionInfo,
    SessionNotification,
    ToolCallProgress,
    ToolCallUpdate,
)
from acpremote import RemoteProxyAgent, TransportOptions, connect_acp, serve_acp
from acpremote.client import RemoteClientConnection


@dataclass(slots=True)
class _RecordingClient:
    updates: list[SessionNotification] = field(default_factory=list)

    async def request_permission(
        self,
        options: list[PermissionOption],
        session_id: str,
        tool_call: ToolCallUpdate,
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        del options, session_id, tool_call, kwargs
        raise AssertionError("permission flow is not used in phase 3")

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.updates.append(
            SessionNotification(session_id=session_id, update=update, field_meta=kwargs or None)
        )

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        del method, params
        raise AssertionError("extension methods are not used on the local client")

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        del method, params

    def on_connect(self, conn: Agent) -> None:
        del conn


@pytest.mark.asyncio
async def test_phase3_recording_client_stub_methods() -> None:
    client = _RecordingClient()

    with pytest.raises(AssertionError, match="permission flow"):
        await client.request_permission([], "session-1", cast(Any, object()))
    with pytest.raises(AssertionError, match="extension methods"):
        await client.ext_method("demo.echo", {"value": 1})

    await client.ext_notification("demo.note", {"value": 2})
    await client.session_update(
        "session-1",
        AgentMessageChunk(session_update="agent_message_chunk", content=text_block("ok")),
        source="phase3",
    )
    assert client.updates[0].field_meta == {"source": "phase3"}
    assert client.on_connect(cast(Agent, object())) is None


@dataclass(slots=True)
class _ProxyTargetAgent:
    ext_notifications: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    session_cwds: list[str] = field(default_factory=list)
    initialize_capabilities: list[ClientCapabilities | None] = field(default_factory=list)
    _conn: Client | None = None

    def on_connect(self, conn: Client) -> None:
        self._conn = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        self.initialize_capabilities.append(client_capabilities)
        del client_info, kwargs
        return InitializeResponse(protocol_version=protocol_version)

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        self.session_cwds.append(cwd)
        del mcp_servers, kwargs
        return NewSessionResponse(session_id="phase3-session")

    async def list_sessions(
        self,
        cursor: str | None = None,
        cwd: str | None = None,
        **kwargs: Any,
    ) -> ListSessionsResponse:
        del cursor, cwd, kwargs
        return ListSessionsResponse(
            sessions=[SessionInfo(cwd="/tmp", session_id="phase3-session", title="Remote session")]
        )

    async def authenticate(self, method_id: str, **kwargs: Any) -> AuthenticateResponse | None:
        del method_id, kwargs
        return AuthenticateResponse()

    async def prompt(
        self,
        prompt: list[Any],
        session_id: str,
        message_id: str | None = None,
        **kwargs: Any,
    ) -> PromptResponse:
        del message_id, kwargs
        text = "".join(block.text for block in prompt if hasattr(block, "text"))
        self.prompts.append(text)
        if self._conn is not None:  # pragma: no branch
            await self._conn.session_update(
                session_id=session_id,
                update=AgentMessageChunk(
                    session_update="agent_message_chunk",
                    content=text_block(text),
                ),
                source="acpremote-phase3",
            )
        return PromptResponse(stop_reason="end_turn")

    async def close_session(
        self,
        session_id: str,
        **kwargs: Any,
    ) -> CloseSessionResponse | None:
        del session_id, kwargs  # pragma: no cover
        return CloseSessionResponse()  # pragma: no cover

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"method": method, "params": params}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        self.ext_notifications.append((method, params))


async def _open_stream_pair() -> tuple[
    tuple[asyncio.StreamReader, asyncio.StreamWriter],
    tuple[asyncio.StreamReader, asyncio.StreamWriter],
]:
    loop = asyncio.get_running_loop()
    accepted: asyncio.Future[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = (
        loop.create_future()
    )

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if not accepted.done():  # pragma: no branch
            accepted.set_result((reader, writer))

    server = await asyncio.start_server(_handle, "127.0.0.1", 0)
    assert server.sockets is not None
    port = server.sockets[0].getsockname()[1]
    client_reader, client_writer = await asyncio.open_connection("127.0.0.1", port)
    server_reader, server_writer = await accepted
    server.close()
    await server.wait_closed()
    return (client_reader, client_writer), (server_reader, server_writer)


@pytest.mark.asyncio
async def test_phase3_connect_acp_returns_local_agent_proxy_with_remote_passthrough() -> None:
    target = _ProxyTargetAgent()
    server = await serve_acp(cast(Agent, target), mount_path="/proxy", remote_cwd="/srv/remote")
    assert server.sockets is not None
    port = server.sockets[0].getsockname()[1]
    proxy = cast(RemoteProxyAgent, connect_acp(f"ws://127.0.0.1:{port}/proxy/ws"))
    client = _RecordingClient()
    proxy.on_connect(cast(Client, client))
    try:
        initialized = await proxy.initialize(protocol_version=1)
        assert initialized.protocol_version == 1

        new_session = await proxy.new_session(cwd="/tmp")
        assert new_session.session_id == "phase3-session"

        listed = await proxy.list_sessions(cwd="/tmp")
        assert listed.sessions[0].session_id == "phase3-session"

        authenticated = await proxy.authenticate(method_id="demo")
        assert isinstance(authenticated, AuthenticateResponse)

        ext_response = await proxy.ext_method("demo.echo", {"value": 1})
        assert ext_response == {"method": "demo.echo", "params": {"value": 1}}

        await proxy.ext_notification("demo.note", {"value": 2})

        prompt_response = await proxy.prompt(
            [text_block("phase3 passthrough")],
            session_id=new_session.session_id,
        )
        assert prompt_response.stop_reason == "end_turn"

    finally:
        await proxy.close()
        server.close()
        await server.wait_closed()

    assert target.prompts == ["phase3 passthrough"]
    assert target.session_cwds == ["/srv/remote"]
    assert len(target.initialize_capabilities) == 1
    initialized_caps = target.initialize_capabilities[0]
    assert isinstance(initialized_caps, ClientCapabilities)
    assert initialized_caps.fs is not None
    assert initialized_caps.fs.read_text_file is False
    assert initialized_caps.fs.write_text_file is False
    assert initialized_caps.terminal is False
    assert target.ext_notifications == [("demo.note", {"value": 2})]
    assert len(client.updates) == 1
    assert client.updates[0].field_meta == {"source": "acpremote-phase3"}
    assert isinstance(client.updates[0].update, AgentMessageChunk)
    assert client.updates[0].update.content.text == "phase3 passthrough"


@pytest.mark.asyncio
async def test_phase3_proxy_requires_on_connect_and_reuses_cached_remote_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = RemoteProxyAgent(url="ws://example.invalid/acp/ws")

    with pytest.raises(RuntimeError, match="requires on_connect"):
        await proxy.initialize(protocol_version=1)

    calls: list[Client] = []

    @dataclass(slots=True)
    class _StubConnection:
        async def initialize(self, protocol_version: int, **kwargs: Any) -> InitializeResponse:
            del kwargs
            return InitializeResponse(protocol_version=protocol_version)

    @dataclass(slots=True)
    class _StubRemote:
        connection: Any = field(default_factory=_StubConnection)
        metadata: Any = None

        async def close(self) -> None:
            return None  # pragma: no cover

    async def _fake_connect_remote_agent(
        client: Client,
        url: str,
        *,
        options: Any,
        headers: Any,
        bearer_token: Any,
    ) -> RemoteClientConnection:
        del url, options, headers, bearer_token
        calls.append(client)
        return cast(RemoteClientConnection, _StubRemote())

    monkeypatch.setattr(
        "acpremote.proxy_agent.connect_remote_agent",
        _fake_connect_remote_agent,
    )

    client = cast(Client, _RecordingClient())
    proxy.on_connect(client)

    first = await proxy.initialize(protocol_version=1)
    second = await proxy.initialize(protocol_version=2)

    assert first.protocol_version == 1
    assert second.protocol_version == 2
    assert calls == [client]


@pytest.mark.asyncio
async def test_phase3_proxy_defaults_to_remote_host_ownership_but_can_opt_into_passthrough() -> (
    None
):
    remote_target = _ProxyTargetAgent()
    remote_server = await serve_acp(cast(Agent, remote_target), mount_path="/remote")
    assert remote_server.sockets is not None
    remote_port = remote_server.sockets[0].getsockname()[1]
    remote_proxy = cast(RemoteProxyAgent, connect_acp(f"ws://127.0.0.1:{remote_port}/remote/ws"))
    remote_proxy.on_connect(cast(Client, _RecordingClient()))

    passthrough_target = _ProxyTargetAgent()
    passthrough_server = await serve_acp(cast(Agent, passthrough_target), mount_path="/pass")
    assert passthrough_server.sockets is not None
    passthrough_port = passthrough_server.sockets[0].getsockname()[1]
    passthrough_proxy = cast(
        RemoteProxyAgent,
        connect_acp(
            f"ws://127.0.0.1:{passthrough_port}/pass/ws",
            options=TransportOptions(host_ownership="client_passthrough"),
        ),
    )
    passthrough_proxy.on_connect(cast(Client, _RecordingClient()))

    capabilities = ClientCapabilities(
        fs=FileSystemCapabilities(read_text_file=True, write_text_file=True),
        terminal=True,
    )

    try:
        await remote_proxy.initialize(protocol_version=1, client_capabilities=capabilities)
        await passthrough_proxy.initialize(protocol_version=1, client_capabilities=capabilities)
    finally:
        await remote_proxy.close()
        remote_server.close()
        await remote_server.wait_closed()
        await passthrough_proxy.close()
        passthrough_server.close()
        await passthrough_server.wait_closed()

    remote_caps = remote_target.initialize_capabilities[0]
    assert isinstance(remote_caps, ClientCapabilities)
    assert remote_caps.fs is not None
    assert remote_caps.fs.read_text_file is False
    assert remote_caps.fs.write_text_file is False
    assert remote_caps.terminal is False

    passthrough_caps = passthrough_target.initialize_capabilities[0]
    assert isinstance(passthrough_caps, ClientCapabilities)
    assert passthrough_caps.fs is not None
    assert passthrough_caps.fs.read_text_file is True
    assert passthrough_caps.fs.write_text_file is True
    assert passthrough_caps.terminal is True


@pytest.mark.asyncio
async def test_phase3_proxy_supports_local_acp_stream_clients_transparently() -> None:
    target = _ProxyTargetAgent()
    remote_server = await serve_acp(cast(Agent, target), mount_path="/proxy")
    assert remote_server.sockets is not None
    remote_port = remote_server.sockets[0].getsockname()[1]

    proxy = connect_acp(f"ws://127.0.0.1:{remote_port}/proxy/ws")
    local_client = _RecordingClient()
    (client_reader, client_writer), (proxy_reader, proxy_writer) = await _open_stream_pair()

    proxy_task = asyncio.create_task(
        run_agent(proxy, input_stream=proxy_writer, output_stream=proxy_reader)
    )
    local_connection = connect_to_agent(cast(Client, local_client), client_writer, client_reader)
    try:
        initialized = await local_connection.initialize(protocol_version=1)
        assert initialized.protocol_version == 1

        session = await local_connection.new_session(cwd="/tmp")
        assert session.session_id == "phase3-session"

        prompt_response = await local_connection.prompt(
            [text_block("stdio passthrough")],
            session_id=session.session_id,
        )
        assert prompt_response.stop_reason == "end_turn"
    finally:
        await local_connection.close()
        client_writer.close()
        proxy_writer.close()
        await client_writer.wait_closed()
        await proxy_writer.wait_closed()
        await cast(RemoteProxyAgent, proxy).close()
        await proxy_task
        remote_server.close()
        await remote_server.wait_closed()

    assert target.prompts == ["stdio passthrough"]
    assert len(local_client.updates) == 1
    assert isinstance(local_client.updates[0].update, AgentMessageChunk)
    assert local_client.updates[0].update.content.text == "stdio passthrough"


@pytest.mark.asyncio
async def test_phase3_proxy_can_emit_transport_latency_meta_and_projection() -> None:
    target = _ProxyTargetAgent()
    remote_server = await serve_acp(cast(Agent, target), mount_path="/latency")
    assert remote_server.sockets is not None
    remote_port = remote_server.sockets[0].getsockname()[1]

    proxy = connect_acp(
        f"ws://127.0.0.1:{remote_port}/latency/ws",
        options=TransportOptions(
            emit_latency_meta=True,
            emit_latency_projection=True,
        ),
    )
    local_client = _RecordingClient()
    proxy.on_connect(cast(Client, local_client))
    try:
        await proxy.initialize(protocol_version=1)
        session = await proxy.new_session(cwd="/tmp")
        response = await proxy.prompt(
            [text_block("latency please")],
            session_id=session.session_id,
        )
        assert response.stop_reason == "end_turn"
    finally:
        await cast(RemoteProxyAgent, proxy).close()
        remote_server.close()
        await remote_server.wait_closed()

    assert len(local_client.updates) == 2

    streamed = local_client.updates[0]
    assert isinstance(streamed.update, AgentMessageChunk)
    assert streamed.update.content.text == "latency please"
    assert streamed.field_meta is not None
    latency_meta = streamed.field_meta["field_meta"]["acpremote"]["transport_latency"]
    assert latency_meta["elapsed_ms"] >= 0
    assert latency_meta["first_update_ms"] >= 0
    assert latency_meta["update_count"] == 1
    assert streamed.field_meta["source"] == "acpremote-phase3"

    projected = local_client.updates[1]
    assert isinstance(projected.update, ToolCallProgress)
    assert projected.update.title == "Transport Latency"
    assert projected.update.kind == "other"
    assert projected.update.status == "completed"
    assert projected.field_meta == {"source": "acpremote-latency"}
    assert projected.update.raw_output is not None
    projected_latency = projected.update.raw_output["acpremote"]["transport_latency"]
    assert projected_latency["total_ms"] >= 0
    assert projected_latency["update_count"] == 1
