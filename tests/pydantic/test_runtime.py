from __future__ import annotations as _annotations

import asyncio

import pytest
from acp import PROTOCOL_VERSION
from acp.exceptions import RequestError

from .support import (
    UTC,
    AcpSessionContext,
    AdapterConfig,
    Agent,
    AgentBridgeBuilder,
    AgentFactory,
    AgentMessageChunk,
    AgentSource,
    ClientFilesystemBackend,
    ClientHostContext,
    ClientTerminalBackend,
    FactoryAgentSource,
    FileSessionStore,
    FilesystemBackend,
    McpBridge,
    McpServerDefinition,
    MemorySessionStore,
    ModelSelectionState,
    ModeState,
    Path,
    RecordingClient,
    StaticAgentSource,
    TerminalBackend,
    TestModel,
    ToolCallProgress,
    ToolCallStart,
    UserMessageChunk,
    create_acp_agent,
    datetime,
    text_block,
)


def test_prompt_and_load_session_replay_history(tmp_path: Path) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="Hello from ACP")),
        config=AdapterConfig(
            agent_name="pydantic-acp",
            agent_title="Pydantic ACP",
            agent_version="0.1.0",
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    initialize_response = asyncio.run(adapter.initialize(protocol_version=1))
    assert initialize_response.agent_info is not None
    assert initialize_response.agent_info.name == "pydantic-acp"
    assert initialize_response.agent_capabilities is not None
    assert initialize_response.agent_capabilities.load_session is True

    new_session_response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    prompt_response = asyncio.run(
        adapter.prompt(
            prompt=[text_block("Summarize the change.")],
            session_id=new_session_response.session_id,
            message_id="user-message-1",
        )
    )

    assert prompt_response.stop_reason == "end_turn"
    assert prompt_response.user_message_id == "user-message-1"
    assert len(client.updates) == 1
    assert client.updates[0][0] == new_session_response.session_id
    agent_update = client.updates[0][1]
    assert isinstance(agent_update, AgentMessageChunk)
    assert agent_update.content.text == "Hello from ACP"

    client.updates.clear()
    load_response = asyncio.run(
        adapter.load_session(
            cwd=str(tmp_path),
            session_id=new_session_response.session_id,
            mcp_servers=[],
        )
    )

    assert load_response is not None
    assert [type(update) for _, update in client.updates] == [
        UserMessageChunk,
        AgentMessageChunk,
    ]

    user_update = client.updates[0][1]
    replayed_agent_update = client.updates[1][1]
    assert isinstance(user_update, UserMessageChunk)
    assert user_update.content.text == "Summarize the change."
    assert isinstance(replayed_agent_update, AgentMessageChunk)
    assert replayed_agent_update.content.text == "Hello from ACP"


def test_initialize_uses_static_agent_name_by_default() -> None:
    adapter = create_acp_agent(agent=Agent(TestModel(), name="demo-agent-name"))

    initialize_response = asyncio.run(adapter.initialize(protocol_version=1))

    assert initialize_response.agent_info is not None
    assert initialize_response.agent_info.name == "demo-agent-name"
    assert initialize_response.agent_info.title == "Pydantic ACP"


def test_initialize_negotiates_protocol_and_exposes_mcp_capabilities() -> None:
    mcp_bridge = McpBridge(
        servers=[
            McpServerDefinition(server_id="http-server", name="HTTP", transport="http"),
            McpServerDefinition(server_id="sse-server", name="SSE", transport="sse"),
        ]
    )
    adapter = create_acp_agent(
        agent=Agent(TestModel()),
        config=AdapterConfig(
            agent_name="configured-agent",
            agent_title="Configured ACP",
            agent_version="9.9.9",
            capability_bridges=[mcp_bridge],
        ),
    )

    initialize_response = asyncio.run(adapter.initialize(protocol_version=PROTOCOL_VERSION + 10))

    assert initialize_response.protocol_version == PROTOCOL_VERSION
    assert initialize_response.agent_info is not None
    assert initialize_response.agent_info.name == "configured-agent"
    assert initialize_response.agent_info.title == "Configured ACP"
    assert initialize_response.agent_info.version == "9.9.9"
    assert initialize_response.agent_capabilities is not None
    assert initialize_response.agent_capabilities.mcp_capabilities is not None
    assert initialize_response.agent_capabilities.mcp_capabilities.http is True
    assert initialize_response.agent_capabilities.mcp_capabilities.sse is True
    assert initialize_response.agent_capabilities.session_capabilities is not None
    assert initialize_response.agent_capabilities.session_capabilities.fork is not None
    assert initialize_response.agent_capabilities.session_capabilities.resume is not None


def test_authenticate_cancel_and_extension_methods_follow_public_contract() -> None:
    adapter = create_acp_agent(agent=Agent(TestModel()))

    assert asyncio.run(adapter.authenticate("demo-method")) is None
    assert asyncio.run(adapter.cancel(session_id="missing-session")) is None
    assert asyncio.run(adapter.ext_notification("demo.notify", {"value": 1})) is None
    with pytest.raises(RequestError):
        asyncio.run(adapter.ext_method("demo.unknown", {"value": 1}))


def test_prompt_projects_tool_calls(tmp_path: Path) -> None:
    tool_model = TestModel(call_tools=["read_file"], custom_output_text="done")
    agent = Agent(tool_model)

    @agent.tool_plain
    def read_file(path: str) -> str:
        return f"contents:{path}"

    adapter = create_acp_agent(
        agent=agent, config=AdapterConfig(session_store=MemorySessionStore())
    )
    client = RecordingClient()
    adapter.on_connect(client)

    new_session_response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Read the file.")],
            session_id=new_session_response.session_id,
        )
    )

    tool_updates = [
        update
        for _, update in client.updates
        if isinstance(update, (ToolCallStart, ToolCallProgress))
    ]
    assert len(tool_updates) == 2

    tool_start = tool_updates[0]
    tool_completion = tool_updates[1]
    assert isinstance(tool_start, ToolCallStart)
    assert tool_start.title == "read_file"
    assert tool_start.kind == "read"
    assert tool_start.raw_input == {"path": "a"}
    assert isinstance(tool_completion, ToolCallProgress)
    assert tool_completion.status == "completed"
    assert tool_completion.raw_output == "contents:a"


def test_file_session_store_round_trip(tmp_path: Path) -> None:
    store = FileSessionStore(tmp_path / "sessions")
    session = AcpSessionContext(
        session_id="session-123",
        cwd=tmp_path,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        title="My Session",
        message_history_json='[{"role":"user"}]',
    )

    store.save(session)
    loaded_session = store.get("session-123")

    assert loaded_session is not None
    assert loaded_session.session_id == "session-123"
    assert loaded_session.title == "My Session"


def test_list_sessions_filters_by_cwd_and_orders_latest_first(tmp_path: Path) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="listed")),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )

    alpha_session = asyncio.run(adapter.new_session(cwd=str(tmp_path / "alpha"), mcp_servers=[]))
    beta_session = asyncio.run(adapter.new_session(cwd=str(tmp_path / "beta"), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Refresh alpha ordering.")],
            session_id=alpha_session.session_id,
        )
    )

    listed_all = asyncio.run(adapter.list_sessions())
    listed_alpha = asyncio.run(adapter.list_sessions(cwd=str(tmp_path / "alpha")))

    assert [session.session_id for session in listed_all.sessions] == [
        alpha_session.session_id,
        beta_session.session_id,
    ]
    assert [session.cwd for session in listed_alpha.sessions] == [str(tmp_path / "alpha")]


def test_public_agent_source_exports_are_available() -> None:
    assert AgentFactory is not None
    assert AgentBridgeBuilder is not None
    assert AgentSource is not None
    assert ClientFilesystemBackend is not None
    assert ClientHostContext is not None
    assert ClientTerminalBackend is not None
    assert FilesystemBackend is not None
    assert FactoryAgentSource is not None
    assert StaticAgentSource is not None
    assert TerminalBackend is not None


def test_public_provider_exports_are_available() -> None:
    assert ModelSelectionState is not None
    assert ModeState is not None


def test_create_acp_agent_requires_exactly_one_source() -> None:
    with pytest.raises(ValueError, match="Exactly one"):
        create_acp_agent()

    with pytest.raises(ValueError, match="Exactly one"):
        create_acp_agent(
            agent=Agent(TestModel()),
            agent_factory=lambda session: Agent(TestModel()),
        )
