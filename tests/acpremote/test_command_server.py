from __future__ import annotations as _annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
from acp import text_block
from acp.interfaces import Client
from acp.schema import (
    AgentMessageChunk,
    PermissionOption,
    RequestPermissionResponse,
    SessionNotification,
    ToolCallUpdate,
)
from acpremote import CommandOptions, connect_remote_agent, serve_command, serve_stdio_command
from acpremote import command as command_module


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
        raise AssertionError("permission flow is not used by command relay tests")

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.updates.append(
            SessionNotification(session_id=session_id, update=update, field_meta=kwargs or None)
        )

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        del method, params
        raise AssertionError("extension methods are not used by command relay tests")

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        del method, params

    def on_connect(self, conn: Any) -> None:
        del conn


@pytest.mark.asyncio
async def test_command_server_recording_client_stub_methods() -> None:
    client = _RecordingClient()

    with pytest.raises(AssertionError, match="permission flow"):
        await client.request_permission([], "session-1", cast(Any, object()))
    with pytest.raises(AssertionError, match="extension methods"):
        await client.ext_method("demo.echo", {"value": 1})

    await client.ext_notification("demo.note", {"value": 2})
    await client.session_update(
        "session-1",
        AgentMessageChunk(session_update="agent_message_chunk", content=text_block("ok")),
        source="test",
    )
    assert client.updates[0].field_meta == {"source": "test"}
    assert client.on_connect(object()) is None


def _write_stdio_acp_script(tmp_path: Path, *, emit_stderr: bool = False) -> Path:
    stderr_line = (
        '    import sys\n    print("stderr ready", file=sys.stderr)\n' if emit_stderr else ""
    )
    script_path = tmp_path / "stdio_acp_server.py"
    script_path.write_text(
        "\n".join(
            (
                "from __future__ import annotations",
                "",
                "import asyncio",
                "import os",
                "from dataclasses import dataclass",
                "from typing import Any",
                "",
                "from acp import run_agent, text_block",
                "from acp.interfaces import Client",
                "from acp.schema import AgentMessageChunk, ClientCapabilities, Implementation, InitializeResponse, NewSessionResponse, PromptResponse",
                "",
                "@dataclass(slots=True)",
                "class EchoAgent:",
                "    _conn: Client | None = None",
                "",
                "    def on_connect(self, conn: Client) -> None:",
                "        self._conn = conn",
                "",
                "    async def initialize(",
                "        self,",
                "        protocol_version: int,",
                "        client_capabilities: ClientCapabilities | None = None,",
                "        client_info: Implementation | None = None,",
                "        **kwargs: Any,",
                "    ) -> InitializeResponse:",
                "        del client_capabilities, client_info, kwargs",
                "        return InitializeResponse(protocol_version=protocol_version)",
                "",
                "    async def new_session(",
                "        self,",
                "        cwd: str,",
                "        mcp_servers: list[Any] | None = None,",
                "        **kwargs: Any,",
                "    ) -> NewSessionResponse:",
                "        del cwd, mcp_servers, kwargs",
                "        return NewSessionResponse(session_id=os.environ['ACPREMOTE_SESSION_ID'])",
                "",
                "    async def prompt(",
                "        self,",
                "        prompt: list[Any],",
                "        session_id: str,",
                "        **kwargs: Any,",
                "    ) -> PromptResponse:",
                "        del kwargs",
                "        text = ''.join(block.text for block in prompt if hasattr(block, 'text'))",
                "        payload = f\"{os.environ['ACPREMOTE_PREFIX']}:{os.getcwd()}:{text}\"",
                "        if self._conn is not None:",
                "            await self._conn.session_update(",
                "                session_id=session_id,",
                "                update=AgentMessageChunk(",
                "                    session_update='agent_message_chunk',",
                "                    content=text_block(payload),",
                "                ),",
                "            )",
                "        return PromptResponse(stop_reason='end_turn')",
                "",
                "async def main() -> None:",
                stderr_line.rstrip("\n"),
                "    await run_agent(EchoAgent())",
                "",
                "asyncio.run(main())",
            )
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return script_path


def test_command_options_reject_empty_command() -> None:
    with pytest.raises(ValueError, match="command must not be empty"):
        CommandOptions(command=())


def test_command_env_overrides_preserve_existing_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("UNCHANGED", "keep")

    merged = command_module._build_process_env({"GOOGLE_API_KEY": "demo"})

    assert merged["PATH"] == "/usr/bin:/bin"
    assert merged["UNCHANGED"] == "keep"
    assert merged["GOOGLE_API_KEY"] == "demo"


@pytest.mark.asyncio
async def test_serve_command_relays_stdio_acp_process(tmp_path: Path) -> None:
    script_path = _write_stdio_acp_script(tmp_path)
    client = _RecordingClient()
    server = await serve_command(
        [sys.executable, str(script_path)],
        mount_path="/command",
        cwd=str(tmp_path),
        env={
            "ACPREMOTE_PREFIX": "relay",
            "ACPREMOTE_SESSION_ID": "command-session",
        },
    )
    assert server.sockets is not None
    port = server.sockets[0].getsockname()[1]
    remote = await connect_remote_agent(
        cast(Client, client),
        f"ws://127.0.0.1:{port}/command/ws",
    )
    try:
        initialized = await remote.connection.initialize(protocol_version=1)
        assert initialized.protocol_version == 1

        session = await remote.connection.new_session(cwd=str(tmp_path))
        assert session.session_id == "command-session"

        prompt_response = await remote.connection.prompt(
            [text_block("hello from ws")],
            session_id=session.session_id,
        )
        assert prompt_response.stop_reason == "end_turn"
    finally:
        await remote.close()
        server.close()
        await server.wait_closed()

    assert len(client.updates) == 1
    assert isinstance(client.updates[0].update, AgentMessageChunk)
    assert client.updates[0].update.content.text == f"relay:{tmp_path}:hello from ws"


@pytest.mark.asyncio
async def test_serve_command_metadata_exposes_remote_cwd(tmp_path: Path) -> None:
    script_path = _write_stdio_acp_script(tmp_path)
    client = _RecordingClient()
    server = await serve_command(
        [sys.executable, str(script_path)],
        mount_path="/command",
        cwd=str(tmp_path),
        env={
            "ACPREMOTE_PREFIX": "meta",
            "ACPREMOTE_SESSION_ID": "command-session",
        },
    )
    assert server.sockets is not None
    port = server.sockets[0].getsockname()[1]
    remote = await connect_remote_agent(
        cast(Client, client),
        f"ws://127.0.0.1:{port}/command/ws",
    )
    try:
        assert remote.metadata is not None
        assert remote.metadata.remote_cwd == str(tmp_path)
    finally:
        await remote.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_serve_stdio_command_supports_discarded_stderr(tmp_path: Path) -> None:
    script_path = _write_stdio_acp_script(tmp_path, emit_stderr=True)
    client = _RecordingClient()
    server = await serve_stdio_command(
        CommandOptions(
            command=(sys.executable, str(script_path)),
            cwd=str(tmp_path),
            env={
                "ACPREMOTE_PREFIX": "discard",
                "ACPREMOTE_SESSION_ID": "discard-session",
            },
            stderr_mode="discard",
        ),
        mount_path="/discard",
    )
    assert server.sockets is not None
    port = server.sockets[0].getsockname()[1]
    remote = await connect_remote_agent(
        cast(Client, client),
        f"ws://127.0.0.1:{port}/discard/ws",
    )
    try:
        await remote.connection.initialize(protocol_version=1)
        session = await remote.connection.new_session(cwd=str(tmp_path))
        await remote.connection.prompt(
            [text_block("stderr hidden")],
            session_id=session.session_id,
        )
    finally:
        await remote.close()
        server.close()
        await server.wait_closed()

    assert len(client.updates) == 1
    assert isinstance(client.updates[0].update, AgentMessageChunk)
    assert client.updates[0].update.content.text == f"discard:{tmp_path}:stderr hidden"
