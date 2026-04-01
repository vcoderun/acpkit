from __future__ import annotations as _annotations

import asyncio

from .support import (
    UTC,
    AcpSessionContext,
    AdapterConfig,
    Agent,
    AgentMessageChunk,
    ClientFilesystemBackend,
    ClientHostContext,
    ClientTerminalBackend,
    EnvVariable,
    FilesystemRecordingClient,
    HostRecordingClient,
    MemorySessionStore,
    Path,
    RunContext,
    TerminalRecordingClient,
    TestModel,
    WaitForTerminalExitResponse,
    create_acp_agent,
    datetime,
    text_block,
)


def test_client_filesystem_backend_reads_with_session_context() -> None:
    session = AcpSessionContext(
        session_id="session-filesystem-read",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    client = FilesystemRecordingClient()
    backend = ClientFilesystemBackend(client=client, session=session)

    response = asyncio.run(
        backend.read_text_file(
            "notes/todo.txt",
            line=4,
            limit=120,
        )
    )

    assert response.content == "file:notes/todo.txt:4:120"
    assert client.read_calls == [("session-filesystem-read", "notes/todo.txt", 120, 4)]


def test_client_filesystem_backend_writes_with_session_context() -> None:
    session = AcpSessionContext(
        session_id="session-filesystem-write",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    client = FilesystemRecordingClient()
    backend = ClientFilesystemBackend(client=client, session=session)

    response = asyncio.run(
        backend.write_text_file(
            "notes/todo.txt",
            "ship milestone 7 phase 1",
        )
    )

    assert response is not None
    assert client.write_calls == [
        ("session-filesystem-write", "notes/todo.txt", "ship milestone 7 phase 1")
    ]


def test_client_filesystem_backend_propagates_missing_write_response() -> None:
    session = AcpSessionContext(
        session_id="session-filesystem-optional-write",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    client = FilesystemRecordingClient()
    client.write_response = None
    backend = ClientFilesystemBackend(client=client, session=session)

    response = asyncio.run(backend.write_text_file("notes/empty.txt", "no-op"))

    assert response is None
    assert client.write_calls == [("session-filesystem-optional-write", "notes/empty.txt", "no-op")]


def test_client_terminal_backend_creates_outputs_and_waits_with_session_context() -> None:
    session = AcpSessionContext(
        session_id="session-terminal-ops",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    client = TerminalRecordingClient()
    backend = ClientTerminalBackend(client=client, session=session)
    create_response = asyncio.run(
        backend.create_terminal(
            "python",
            args=["-V"],
            cwd="/workspace",
            env=[EnvVariable(name="MODE", value="demo")],
            output_byte_limit=4096,
        )
    )
    output_response = asyncio.run(backend.terminal_output(create_response.terminal_id))
    wait_response = asyncio.run(backend.wait_for_terminal_exit(create_response.terminal_id))

    assert create_response.terminal_id == "terminal-1"
    assert output_response.output == "terminal-output"
    assert output_response.truncated is False
    assert wait_response.exit_code == 0
    assert client.create_calls == [
        (
            "session-terminal-ops",
            "python",
            ["-V"],
            "/workspace",
            [EnvVariable(name="MODE", value="demo")],
            4096,
        )
    ]
    assert client.output_calls == [("session-terminal-ops", "terminal-1")]
    assert client.wait_calls == [("session-terminal-ops", "terminal-1")]


def test_client_terminal_backend_releases_and_kills_with_session_context() -> None:
    session = AcpSessionContext(
        session_id="session-terminal-cleanup",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    client = TerminalRecordingClient()
    backend = ClientTerminalBackend(client=client, session=session)

    release_response = asyncio.run(backend.release_terminal("terminal-9"))
    kill_response = asyncio.run(backend.kill_terminal("terminal-9"))

    assert release_response is not None
    assert kill_response is not None
    assert client.release_calls == [("session-terminal-cleanup", "terminal-9")]
    assert client.kill_calls == [("session-terminal-cleanup", "terminal-9")]


def test_client_terminal_backend_propagates_optional_release_and_signal_wait() -> None:
    session = AcpSessionContext(
        session_id="session-terminal-signal",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    client = TerminalRecordingClient()
    client.release_response = None
    client.wait_response = WaitForTerminalExitResponse(exit_code=None, signal="SIGTERM")
    backend = ClientTerminalBackend(client=client, session=session)

    release_response = asyncio.run(backend.release_terminal("terminal-2"))
    wait_response = asyncio.run(backend.wait_for_terminal_exit("terminal-2"))

    assert release_response is None
    assert wait_response.exit_code is None
    assert wait_response.signal == "SIGTERM"
    assert client.release_calls == [("session-terminal-signal", "terminal-2")]
    assert client.wait_calls == [("session-terminal-signal", "terminal-2")]


def test_client_terminal_backend_propagates_missing_kill_response() -> None:
    session = AcpSessionContext(
        session_id="session-terminal-optional-kill",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    client = TerminalRecordingClient()
    client.kill_response = None
    backend = ClientTerminalBackend(client=client, session=session)

    response = asyncio.run(backend.kill_terminal("terminal-3"))

    assert response is None
    assert client.kill_calls == [("session-terminal-optional-kill", "terminal-3")]


def test_client_host_context_builds_session_scoped_backends() -> None:
    session = AcpSessionContext(
        session_id="session-host-context",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    client = HostRecordingClient()

    host_context = ClientHostContext.from_session(
        client=client,
        session=session,
    )

    assert host_context.client is client
    assert host_context.session is session
    assert host_context.filesystem.client is client
    assert host_context.filesystem.session is session
    assert host_context.terminal.client is client
    assert host_context.terminal.session is session


def test_client_host_context_delegates_filesystem_and_terminal_calls() -> None:
    session = AcpSessionContext(
        session_id="session-host-runtime",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    client = HostRecordingClient()
    host_context = ClientHostContext.from_session(client=client, session=session)

    read_response = asyncio.run(host_context.filesystem.read_text_file("notes.txt"))
    create_response = asyncio.run(host_context.terminal.create_terminal("python", args=["-V"]))
    output_response = asyncio.run(
        host_context.terminal.terminal_output(create_response.terminal_id)
    )

    assert read_response.content == "file:notes.txt:None:None"
    assert create_response.terminal_id == "terminal-1"
    assert output_response.output == "terminal-output"
    assert client.read_calls == [("session-host-runtime", "notes.txt", None, None)]
    assert client.create_calls == [("session-host-runtime", "python", ["-V"], None, None, None)]
    assert client.output_calls == [("session-host-runtime", "terminal-1")]


def test_agent_factory_can_build_client_host_context(tmp_path: Path) -> None:
    client = HostRecordingClient()

    def factory(session: AcpSessionContext) -> Agent[None, str]:
        host_context = ClientHostContext.from_session(client=client, session=session)
        agent = Agent(TestModel(call_tools=["read_workspace_note"]))

        @agent.tool
        async def read_workspace_note(ctx: RunContext[None]) -> str:
            del ctx
            response = await host_context.filesystem.read_text_file("notes/factory.txt")
            return response.content

        return agent

    adapter = create_acp_agent(
        agent_factory=factory,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path / "host-factory"), mcp_servers=[]))
    prompt_response = asyncio.run(
        adapter.prompt(
            prompt=[text_block("Read the workspace note through the host context.")],
            session_id=session.session_id,
        )
    )

    assert prompt_response.stop_reason == "end_turn"
    assert client.read_calls == [(session.session_id, "notes/factory.txt", None, None)]
    agent_messages = [
        update.content.text for _, update in client.updates if isinstance(update, AgentMessageChunk)
    ]
    assert agent_messages == ['{"read_workspace_note":"file:notes/factory.txt:None:None"}']
