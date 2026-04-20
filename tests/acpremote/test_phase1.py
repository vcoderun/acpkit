from __future__ import annotations as _annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from acp import text_block, update_agent_message
from acp.exceptions import RequestError
from acp.interfaces import Agent, Client
from acp.schema import (
    AgentMessageChunk,
    AudioContentBlock,
    ClientCapabilities,
    EmbeddedResourceContentBlock,
    HttpMcpServer,
    ImageContentBlock,
    Implementation,
    InitializeResponse,
    McpServerStdio,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    RequestPermissionResponse,
    ResourceContentBlock,
    SessionNotification,
    SseMcpServer,
    TextContentBlock,
    ToolCallUpdate,
)
from acpremote import TransportOptions, connect_remote_agent, serve_remote_agent
from acpremote.auth import bearer_headers
from acpremote.client import _merge_headers
from acpremote.metadata import TransportMetadata
from acpremote.stream import open_websocket_stream_bridge
from websockets.exceptions import ConnectionClosedOK


@dataclass(slots=True)
class _FakeWebSocket:
    incoming: asyncio.Queue[str | bytes] = field(default_factory=asyncio.Queue)
    sent: list[str] = field(default_factory=list)
    send_error: Exception | None = None
    closed: bool = False
    wait_closed_calls: int = 0

    async def recv(self, decode: bool | None = None) -> str | bytes:
        del decode
        if self.closed and self.incoming.empty():
            raise ConnectionClosedOK(None, None)
        item = await self.incoming.get()
        return item

    async def send(self, message: str) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(message)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        del code, reason
        self.closed = True

    async def wait_closed(self) -> None:
        self.wait_closed_calls += 1


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
        raise AssertionError("permission flow is not used in phase 1")

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.updates.append(
            SessionNotification(session_id=session_id, update=update, field_meta=kwargs or None)
        )

    async def write_text_file(
        self,
        content: str,
        path: str,
        session_id: str,
        **kwargs: Any,
    ) -> None:
        del content, path, session_id, kwargs
        raise AssertionError("filesystem flow is not used in phase 1")

    async def read_text_file(
        self,
        path: str,
        session_id: str,
        limit: int | None = None,
        line: int | None = None,
        **kwargs: Any,
    ) -> Any:
        del path, session_id, limit, line, kwargs
        raise AssertionError("filesystem flow is not used in phase 1")

    async def create_terminal(
        self,
        command: str,
        session_id: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: list[Any] | None = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> Any:
        del command, session_id, args, cwd, env, output_byte_limit, kwargs
        raise AssertionError("terminal flow is not used in phase 1")

    async def terminal_output(self, session_id: str, terminal_id: str, **kwargs: Any) -> Any:
        del session_id, terminal_id, kwargs
        raise AssertionError("terminal flow is not used in phase 1")

    async def release_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> None:
        del session_id, terminal_id, kwargs
        raise AssertionError("terminal flow is not used in phase 1")

    async def wait_for_terminal_exit(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> Any:
        del session_id, terminal_id, kwargs
        raise AssertionError("terminal flow is not used in phase 1")

    async def kill_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> None:
        del session_id, terminal_id, kwargs
        raise AssertionError("terminal flow is not used in phase 1")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        del params
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        del method, params

    def on_connect(self, conn: Agent) -> None:
        del conn


@pytest.mark.asyncio
async def test_phase1_recording_client_stub_methods_cover_error_paths() -> None:
    client = _RecordingClient()

    with pytest.raises(AssertionError, match="permission flow"):
        await client.request_permission([], "session-1", cast(Any, object()))
    with pytest.raises(AssertionError, match="filesystem flow"):
        await client.write_text_file("content", "/tmp/demo", "session-1")
    with pytest.raises(AssertionError, match="filesystem flow"):
        await client.read_text_file("/tmp/demo", "session-1")
    with pytest.raises(AssertionError, match="terminal flow"):
        await client.create_terminal("echo hi", "session-1")
    with pytest.raises(AssertionError, match="terminal flow"):
        await client.terminal_output("session-1", "terminal-1")
    with pytest.raises(AssertionError, match="terminal flow"):
        await client.release_terminal("session-1", "terminal-1")
    with pytest.raises(AssertionError, match="terminal flow"):
        await client.wait_for_terminal_exit("session-1", "terminal-1")
    with pytest.raises(AssertionError, match="terminal flow"):
        await client.kill_terminal("session-1", "terminal-1")

    update = AgentMessageChunk(session_update="agent_message_chunk", content=text_block("ok"))
    await client.session_update("session-1", update, source="phase1")
    assert client.updates[0].field_meta == {"source": "phase1"}
    with pytest.raises(RequestError):
        await client.ext_method("demo.missing", {})
    await client.ext_notification("demo.note", {"value": 1})
    assert client.on_connect(cast(Agent, object())) is None


@dataclass(slots=True)
class _EchoAgent:
    notifications_sent: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
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
        del client_capabilities, client_info, kwargs
        return InitializeResponse(protocol_version=protocol_version)

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio],
        **kwargs: Any,
    ) -> NewSessionResponse:
        del cwd, mcp_servers, kwargs
        return NewSessionResponse(session_id="phase1-session")

    async def prompt(
        self,
        prompt: list[
            TextContentBlock
            | ImageContentBlock
            | AudioContentBlock
            | ResourceContentBlock
            | EmbeddedResourceContentBlock
        ],
        session_id: str,
        **kwargs: Any,
    ) -> PromptResponse:
        del kwargs
        text = "".join(block.text for block in prompt if isinstance(block, TextContentBlock))
        self.prompts.append(text)
        if self._conn is not None:  # pragma: no branch
            await self._conn.session_update(
                session_id=session_id,
                update=update_agent_message(text_block(text)),
                source="acpremote-phase1",
            )
            self.notifications_sent.append(text)
        return PromptResponse(stop_reason="end_turn")


@pytest.mark.asyncio
async def test_phase1_round_trip_supports_initialize_prompt_and_session_update() -> None:
    agent = _EchoAgent()
    server = await serve_remote_agent(cast(Agent, agent))
    assert server.sockets is not None
    port = server.sockets[0].getsockname()[1]
    client = _RecordingClient()
    remote = await connect_remote_agent(cast(Client, client), f"ws://127.0.0.1:{port}/acp/ws")
    try:
        response = await remote.connection.initialize(protocol_version=1)
        assert response.protocol_version == 1
        session = await remote.connection.new_session(cwd="/tmp")
        assert session.session_id == "phase1-session"
        prompt_response = await remote.connection.prompt(
            [text_block("hello over websocket")],
            session_id=session.session_id,
        )
        assert prompt_response.stop_reason == "end_turn"
    finally:
        await remote.close()
        server.close()
        await server.wait_closed()

    assert agent.prompts == ["hello over websocket"]
    assert agent.notifications_sent == ["hello over websocket"]
    assert len(client.updates) == 1
    update = client.updates[0]
    assert update.session_id == "phase1-session"
    assert isinstance(update.update, AgentMessageChunk)
    assert update.update.content.text == "hello over websocket"
    assert update.field_meta == {"source": "acpremote-phase1"}


def test_phase1_helpers_cover_header_merge_and_transport_defaults() -> None:
    assert bearer_headers(None) is None
    assert bearer_headers("   ") is None
    assert bearer_headers("token") == {"Authorization": "Bearer token"}
    assert _merge_headers(None, {"Authorization": "Bearer token"}) == {
        "Authorization": "Bearer token"
    }
    assert _merge_headers({"X-Test": "1"}, {"Authorization": "Bearer token"}) == [
        ("X-Test", "1"),
        ("Authorization", "Bearer token"),
    ]
    assert _merge_headers([("X-Test", "1")], {"Authorization": "Bearer token"}) == [
        ("X-Test", "1"),
        ("Authorization", "Bearer token"),
    ]

    options = TransportOptions()
    assert options.compression is None
    assert options.reader_limit == 1_048_576
    assert options.max_size == 1_048_576
    assert options.max_queue == 16
    assert TransportMetadata().transport_kind == "websocket"
    assert TransportMetadata().transport_version == 1


@pytest.mark.asyncio
async def test_phase1_stream_bridge_handles_partial_lines_and_binary_frame_failures() -> None:
    websocket = _FakeWebSocket()
    await websocket.incoming.put("from remote")
    bridge = await open_websocket_stream_bridge(cast(Any, websocket))

    bridge.writer.write(b"partial")
    await bridge.writer.drain()
    assert websocket.sent == []

    bridge.writer.write(b"-line\n")
    await bridge.writer.drain()
    assert websocket.sent == ["partial-line"]
    assert await bridge.reader.readline() == b"from remote\n"
    await websocket.incoming.put(b"binary-not-supported")

    with pytest.raises(TypeError, match="binary WebSocket frames are not supported"):
        await bridge.close()

    assert websocket.closed is True
    assert websocket.wait_closed_calls == 1


@pytest.mark.asyncio
async def test_phase1_stream_bridge_supports_large_lines_with_configured_reader_limit() -> None:
    websocket = _FakeWebSocket()
    bridge = await open_websocket_stream_bridge(cast(Any, websocket), reader_limit=1_048_576)
    large_message = "x" * 70_000
    try:
        await websocket.incoming.put(large_message)
        line = await bridge.reader.readline()
    finally:
        await websocket.close()
        await websocket.incoming.put("")
        await bridge.close()

    assert line == large_message.encode("utf-8") + b"\n"


@pytest.mark.asyncio
async def test_phase1_stream_bridge_handles_sender_failures_and_transport_edges() -> None:
    websocket = _FakeWebSocket(send_error=RuntimeError("send failed"))
    bridge = await open_websocket_stream_bridge(cast(Any, websocket))

    assert bridge.writer.transport.can_write_eof() is False
    assert bridge.writer.transport.get_extra_info("missing", "fallback") == "fallback"

    bridge.writer.writelines([b"boom", b"\n"])
    with pytest.raises(RuntimeError, match="send failed"):
        await bridge.writer.drain()
    with pytest.raises(RuntimeError, match="send failed"):
        await bridge.writer.wait_closed()

    bridge.writer.transport.write_eof()
    with pytest.raises(ConnectionResetError, match="transport is closing"):
        bridge.writer.write(b"later\n")

    bridge._reader_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await bridge._reader_task
