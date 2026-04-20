from __future__ import annotations as _annotations

import asyncio
import http.client
import json
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import urlsplit

import pytest
from acp import text_block
from acp.interfaces import Agent, Client
from acp.schema import (
    AgentMessageChunk,
    ClientCapabilities,
    Implementation,
    InitializeResponse,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    RequestPermissionResponse,
    SessionNotification,
    ToolCallUpdate,
)
from acpremote import (
    ServerOptions,
    build_server_metadata,
    build_server_paths,
    connect_remote_agent,
    normalize_mount_path,
    serve_acp,
    serve_remote_agent,
)
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus


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
        raise AssertionError("permission flow is not used in phase 2")

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.updates.append(
            SessionNotification(session_id=session_id, update=update, field_meta=kwargs or None)
        )

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        del method, params
        raise AssertionError("extension methods are not used in phase 2")

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        del method, params

    def on_connect(self, conn: Agent) -> None:
        del conn


@dataclass(slots=True)
class _EchoAgent:
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
        mcp_servers: list[Any],
        **kwargs: Any,
    ) -> NewSessionResponse:
        del cwd, mcp_servers, kwargs
        return NewSessionResponse(session_id="phase2-session")

    async def prompt(
        self,
        prompt: list[Any],
        session_id: str,
        **kwargs: Any,
    ) -> PromptResponse:
        del kwargs
        text = "".join(block.text for block in prompt if hasattr(block, "text"))
        if self._conn is not None:
            await self._conn.session_update(
                session_id=session_id,
                update=AgentMessageChunk(
                    session_update="agent_message_chunk",
                    content=text_block(text),
                ),
            )
        return PromptResponse(stop_reason="end_turn")


def _http_get(url: str) -> tuple[int, bytes]:
    parsed = urlsplit(url)
    assert parsed.hostname is not None
    assert parsed.port is not None
    path = parsed.path or "/"
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_phase2_server_path_and_metadata_helpers() -> None:
    assert normalize_mount_path("acp") == "/acp"
    assert normalize_mount_path("/acp/") == "/acp"
    assert normalize_mount_path("/") == "/"
    with pytest.raises(ValueError, match="mount_path must not be empty"):
        normalize_mount_path("   ")

    assert build_server_paths("/acp/").metadata_path == "/acp"
    assert build_server_paths("/acp/").websocket_path == "/acp/ws"
    assert build_server_paths("/").websocket_path == "/ws"

    metadata = build_server_metadata(
        ServerOptions(
            mount_path="/transport/",
            bearer_token="secret",
            supported_agent_families=("pydantic-acp", "langchain-acp"),
            remote_cwd="/srv/app",
        )
    )
    assert metadata.auth_required is True
    assert metadata.supported_auth_modes == ("bearer",)
    assert metadata.metadata_path == "/transport"
    assert metadata.websocket_path == "/transport/ws"
    assert metadata.supported_agent_families == ("pydantic-acp", "langchain-acp")
    assert metadata.remote_cwd == "/srv/app"
    assert metadata.to_json_dict()["transport_kind"] == "websocket"


@pytest.mark.asyncio
async def test_phase2_http_metadata_and_health_routes_are_served() -> None:
    server = await serve_acp(
        cast(Agent, _EchoAgent()),
        mount_path="/transport/",
        supported_agent_families=("generic-acp",),
    )
    try:
        assert server.sockets is not None
        port = server.sockets[0].getsockname()[1]
        health_status, health_body = await asyncio.to_thread(
            _http_get,
            f"http://127.0.0.1:{port}/healthz",
        )
        metadata_status, metadata_body = await asyncio.to_thread(
            _http_get,
            f"http://127.0.0.1:{port}/transport",
        )
        missing_status, missing_body = await asyncio.to_thread(
            _http_get,
            f"http://127.0.0.1:{port}/missing",
        )
    finally:
        server.close()
        await server.wait_closed()

    assert health_status == 200
    assert health_body == b"ok\n"
    assert metadata_status == 200
    payload = json.loads(metadata_body.decode("utf-8"))
    assert payload["auth_required"] is False
    assert payload["metadata_path"] == "/transport"
    assert payload["websocket_path"] == "/transport/ws"
    assert payload["supported_agent_families"] == ["generic-acp"]
    assert payload["remote_cwd"] is None
    assert missing_status == 404
    assert missing_body == b"not found\n"


@pytest.mark.asyncio
async def test_phase2_websocket_path_and_bearer_auth_are_enforced() -> None:
    server = await serve_acp(
        cast(Agent, _EchoAgent()),
        mount_path="/secure",
        bearer_token="secret-token",
        supported_agent_families=("generic-acp",),
    )
    assert server.sockets is not None
    port = server.sockets[0].getsockname()[1]
    try:
        with pytest.raises(InvalidStatus) as missing_token:
            async with connect(f"ws://127.0.0.1:{port}/secure/ws"):
                pass
        assert missing_token.value.response.status_code == 401

        with pytest.raises(InvalidStatus) as wrong_path:
            async with connect(
                f"ws://127.0.0.1:{port}/wrong/ws",
                additional_headers={"Authorization": "Bearer secret-token"},
            ):
                pass
        assert wrong_path.value.response.status_code == 404

        client = _RecordingClient()
        remote = await connect_remote_agent(
            cast(Client, client),
            f"ws://127.0.0.1:{port}/secure/ws",
            bearer_token="secret-token",
        )
        try:
            initialized = await remote.connection.initialize(protocol_version=1)
            assert initialized.protocol_version == 1
            session = await remote.connection.new_session(cwd="/tmp")
            await remote.connection.prompt(
                [text_block("phase2 auth success")],
                session_id=session.session_id,
            )
        finally:
            await remote.close()
    finally:
        server.close()
        await server.wait_closed()

    assert len(client.updates) == 1
    assert isinstance(client.updates[0].update, AgentMessageChunk)
    assert client.updates[0].update.content.text == "phase2 auth success"


@pytest.mark.asyncio
async def test_phase2_rejects_mixed_server_and_transport_options() -> None:
    with pytest.raises(ValueError, match="pass either options or server_options, not both"):
        await serve_remote_agent(
            cast(Agent, _EchoAgent()),
            options=ServerOptions().transport,
            server_options=ServerOptions(),
        )
