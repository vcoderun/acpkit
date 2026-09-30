from __future__ import annotations as _annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from acp.interfaces import Client as AcpClient
from acp.schema import (
    AcpMcpServer,
    AgentPlanUpdate,
    AudioContentBlock,
    AvailableCommand,
    AvailableCommandsUpdate,
    BlobResourceContents,
    ClientCapabilities,
    ClientSessionCapabilities,
    ConfigOptionUpdate,
    ContentToolCallContent,
    CurrentModeUpdate,
    EmbeddedResourceContentBlock,
    PlanEntry,
    PlanEntryPriority,
    PlanEntryStatus,
    ResourceContentBlock,
    SessionConfigOptionsCapabilities,
    SessionMode,
    TerminalToolCallContent,
    TextResourceContents,
    ToolCallLocation,
)
from deepagents import create_deep_agent
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_acp import (
    AdapterConfig,
    AdapterModel,
    AdapterPromptCapabilities,
    BrowserProjectionMap,
    CapabilityBridge,
    CommandProjectionMap,
    CommunityFileManagementProjectionMap,
    DeepAgentsCompatibilityBridge,
    DeepAgentsProjectionMap,
    FileSystemProjectionMap,
    FinanceProjectionMap,
    HttpRequestProjectionMap,
    StaticSlashCommand,
    StaticSlashCommandProvider,
    StructuredEventProjectionMap,
    WebSearchProjectionMap,
    create_acp_agent,
    native_plan_tools,
)
from langchain_acp.providers import ModelSelectionState, ModeState
from langchain_acp.slash import SlashCommandResult
from langchain_core.messages import AIMessage
from langgraph.graph import START, StateGraph

from .support import (
    FileEditToolCallContent,
    GenericFakeChatModel,
    RecordingACPClient,
    ToolCallProgress,
    ToolCallStart,
    agent_message_texts,
    text_block,
)


def test_langchain_acp_streams_tool_projection_and_text(tmp_path) -> None:
    def read_file(path: str) -> str:
        """Read a file from the workspace."""
        return f"contents:{path}"

    fake_model = GenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"path": "notes.txt"},
                            "id": "tool-1",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="Done reading."),
            ],
        ),
    )
    graph = create_agent(model=fake_model, tools=[read_file], name="reader")
    adapter = create_acp_agent(
        graph=graph,
        config=AdapterConfig(
            projection_maps=[
                FileSystemProjectionMap(read_tool_names=frozenset({"read_file"})),
            ],
        ),
    )
    client = RecordingACPClient()
    adapter.on_connect(cast("AcpClient", client))

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.prompt(prompt=[text_block("read notes.txt")], session_id=session.session_id),
    )

    assert response.stop_reason == "end_turn"
    tool_start = next(update for _, update in client.updates if isinstance(update, ToolCallStart))
    tool_progress = next(
        update for _, update in client.updates if isinstance(update, ToolCallProgress)
    )
    assert tool_start.title == "Read `notes.txt`"
    assert tool_progress.status == "completed"
    assert tool_progress.content is not None
    diff = tool_progress.content[0]
    assert isinstance(diff, FileEditToolCallContent)
    assert diff.path == "notes.txt"
    assert diff.new_text == "contents:notes.txt"
    assert agent_message_texts(client) == ["Done reading."]


def test_langchain_acp_initialize_uses_configured_prompt_capabilities() -> None:
    graph = create_agent(
        model=GenericFakeChatModel(messages=iter([])),
        tools=[],
        name="caps",
    )
    adapter = create_acp_agent(
        graph=graph,
        config=AdapterConfig(
            prompt_capabilities=AdapterPromptCapabilities(
                audio=False,
                image=False,
                embedded_context=True,
            ),
        ),
    )

    response = asyncio.run(adapter.initialize(protocol_version=1))
    assert response.agent_capabilities is not None
    assert response.agent_capabilities.prompt_capabilities is not None

    assert response.agent_capabilities.prompt_capabilities.audio is False
    assert response.agent_capabilities.prompt_capabilities.image is False
    assert response.agent_capabilities.prompt_capabilities.embedded_context is True
    assert response.agent_capabilities.mcp_capabilities is not None
    assert response.agent_capabilities.mcp_capabilities.acp is False


def test_acp_mcp_server_input_is_persisted_without_enabling_acp_transport(tmp_path: Path) -> None:
    adapter = create_acp_agent(
        graph=create_agent(
            model=GenericFakeChatModel(messages=iter([])),
            tools=[],
            name="acp-mcp",
        ),
        config=AdapterConfig(),
    )
    server = AcpMcpServer.model_validate(
        {
            "_meta": {"source": "host"},
            "serverId": "delegated-agent",
            "name": "Delegated agent",
            "type": "acp",
        },
    )

    session = asyncio.run(
        adapter.new_session(cwd=str(tmp_path), mcp_servers=[server]),
    )
    loaded = asyncio.run(
        adapter.load_session(cwd=str(tmp_path), session_id=session.session_id),
    )
    stored_session = adapter._store.get(session.session_id)

    assert loaded is not None
    assert stored_session is not None
    assert stored_session.mcp_servers == [
        {
            "_meta": {"source": "host"},
            "serverId": "delegated-agent",
            "name": "Delegated agent",
            "type": "acp",
        },
    ]


def test_client_config_capabilities_gate_langchain_session_options(tmp_path) -> None:
    adapter = create_acp_agent(
        graph=create_agent(
            model=GenericFakeChatModel(messages=iter([])),
            tools=[],
            name="config-capabilities",
        ),
        config=AdapterConfig(
            available_models=[AdapterModel(model_id="base", name="Base")],
            available_modes=[SessionMode(id="ask", name="Ask")],
            default_model_id="base",
            default_mode_id="ask",
        ),
    )
    client = RecordingACPClient()
    adapter.on_connect(cast("AcpClient", client))

    asyncio.run(
        adapter.initialize(
            protocol_version=1,
            client_capabilities=ClientCapabilities(
                session=ClientSessionCapabilities(
                    config_options=SessionConfigOptionsCapabilities(),
                ),
            ),
        ),
    )
    select_only = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    assert select_only.config_options is not None
    assert [option.id for option in select_only.config_options] == ["mode", "model"]

    asyncio.run(
        adapter.initialize(
            protocol_version=1,
            client_capabilities=ClientCapabilities(session=ClientSessionCapabilities()),
        ),
    )
    unsupported = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    assert unsupported.config_options is None
    available_commands = [
        update for _, update in client.updates if isinstance(update, AvailableCommandsUpdate)
    ]
    assert all(command.name != "model" for command in available_commands[-1].available_commands)


def test_langchain_acp_slash_commands_emit_surface_updates_and_skip_graph(
    tmp_path,
) -> None:
    def read_file(path: str) -> str:
        """Read a file from the workspace."""
        return path

    assert read_file("repo.txt") == "repo.txt"

    graph = create_agent(
        model=GenericFakeChatModel(messages=iter([])),
        tools=[read_file],
        name="slash-reader",
    )
    adapter = create_acp_agent(
        graph=graph,
        config=AdapterConfig(
            available_models=[
                AdapterModel(model_id="openai:gpt-5-mini", name="GPT-5 Mini"),
                AdapterModel(model_id="openai:gpt-5", name="GPT-5"),
            ],
            available_modes=[
                SessionMode(id="ask", name="Ask"),
                SessionMode(id="review", name="Review"),
            ],
            default_model_id="openai:gpt-5-mini",
            default_mode_id="ask",
            slash_command_provider=StaticSlashCommandProvider(
                commands=[
                    StaticSlashCommand(
                        command=AvailableCommand(name="ping", description="Return pong."),
                        handler=lambda _request: SlashCommandResult(text="pong"),
                    ),
                ],
            ),
        ),
    )
    client = RecordingACPClient()
    adapter.on_connect(cast("AcpClient", client))

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    stored_session = cast("Any", adapter)._store.get(session.session_id)
    assert stored_session is not None
    stored_session.mcp_servers = [
        {"name": "repo-http", "transport": "http", "url": "https://repo.example/mcp"},
    ]
    cast("Any", adapter)._store.save(stored_session)
    client.updates.clear()

    asyncio.run(adapter.prompt(prompt=[text_block("/tools")], session_id=session.session_id))
    asyncio.run(adapter.prompt(prompt=[text_block("/review")], session_id=session.session_id))
    asyncio.run(
        adapter.prompt(prompt=[text_block("/model openai:gpt-5")], session_id=session.session_id),
    )
    asyncio.run(adapter.prompt(prompt=[text_block("/mcp-servers")], session_id=session.session_id))
    asyncio.run(adapter.prompt(prompt=[text_block("/ping")], session_id=session.session_id))

    assert agent_message_texts(client) == [
        "Available tools:\n- read_file: Read a file from the workspace.",
        "Current mode: review",
        "Current model: openai:gpt-5",
        "MCP servers:\n- repo-http (http, session): https://repo.example/mcp",
        "pong",
    ]
    assert any(isinstance(update, CurrentModeUpdate) for _, update in client.updates)
    assert any(isinstance(update, ConfigOptionUpdate) for _, update in client.updates)
    available_command_updates = [
        update for _, update in client.updates if isinstance(update, AvailableCommandsUpdate)
    ]
    assert available_command_updates
    assert [command.name for command in available_command_updates[0].available_commands] == [
        "ask",
        "review",
        "model",
        "tools",
        "mcp-servers",
        "ping",
    ]


def test_langchain_acp_projects_web_search_results_at_runtime(tmp_path) -> None:
    def duckduckgo_results_json(query: str) -> list[dict[str, str]]:
        """Search the web and return result records."""
        return [
            {
                "title": "ACP Kit",
                "url": "https://example.com/acpkit",
                "snippet": "Truthful ACP adapters.",
            },
        ]

    fake_model = GenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "duckduckgo_results_json",
                            "args": {"query": "acpkit"},
                            "id": "tool-search",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="Search finished."),
            ],
        ),
    )
    graph = create_agent(model=fake_model, tools=[duckduckgo_results_json], name="web-search")
    adapter = create_acp_agent(
        graph=graph,
        config=AdapterConfig(projection_maps=[WebSearchProjectionMap()]),
    )
    client = RecordingACPClient()
    adapter.on_connect(cast("AcpClient", client))

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.prompt(prompt=[text_block("search acpkit")], session_id=session.session_id),
    )

    assert response.stop_reason == "end_turn"
    tool_start = next(update for _, update in client.updates if isinstance(update, ToolCallStart))
    tool_progress = next(
        update for _, update in client.updates if isinstance(update, ToolCallProgress)
    )
    assert tool_start.kind == "search"
    assert tool_start.title == "Search web for acpkit"
    assert tool_start.content is not None
    assert isinstance(tool_start.content[0], ContentToolCallContent)
    assert tool_start.content[0].content.text == "Query: acpkit"
    assert tool_progress.kind == "search"
    assert tool_progress.content is not None
    assert isinstance(tool_progress.content[0], ContentToolCallContent)
    assert "1. ACP Kit" in tool_progress.content[0].content.text
    assert "https://example.com/acpkit" in tool_progress.content[0].content.text
    assert agent_message_texts(client) == ["Search finished."]


def test_langchain_acp_projects_web_fetch_results_at_runtime(tmp_path) -> None:
    def requests_get(url: str) -> str:
        """Fetch a page body from the web."""
        return f"Fetched body from {url}"

    fake_model = GenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "requests_get",
                            "args": {"url": "https://example.com/docs"},
                            "id": "tool-fetch",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="Fetch finished."),
            ],
        ),
    )
    graph = create_agent(model=fake_model, tools=[requests_get], name="web-fetch")
    adapter = create_acp_agent(
        graph=graph,
        config=AdapterConfig(projection_maps=[HttpRequestProjectionMap()]),
    )
    client = RecordingACPClient()
    adapter.on_connect(cast("AcpClient", client))

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.prompt(prompt=[text_block("fetch the docs")], session_id=session.session_id),
    )

    assert response.stop_reason == "end_turn"
    tool_start = next(update for _, update in client.updates if isinstance(update, ToolCallStart))
    tool_progress = next(
        update for _, update in client.updates if isinstance(update, ToolCallProgress)
    )
    assert tool_start.kind == "fetch"
    assert tool_start.title == "GET https://example.com/docs"
    assert tool_start.content is not None
    assert isinstance(tool_start.content[0], ContentToolCallContent)
    assert tool_start.content[0].content.text == "URL: https://example.com/docs"
    assert tool_progress.kind == "fetch"
    assert tool_progress.content is not None
    assert isinstance(tool_progress.content[0], ContentToolCallContent)
    assert tool_progress.content[0].content.text == "Fetched body from https://example.com/docs"
    assert agent_message_texts(client) == ["Fetch finished."]


def test_langchain_acp_projects_browser_and_terminal_updates_at_runtime(
    tmp_path,
) -> None:
    def navigate_browser(url: str) -> str:
        """Navigate a browser to the specified URL."""
        return f"Navigating to {url} returned status code 200"

    def extract_hyperlinks() -> str:
        """Extract all hyperlinks on the current webpage."""
        return '["https://example.com/docs", "https://example.com/blog"]'

    def terminal(commands: list[str]) -> str:
        """Run shell commands on this machine."""
        return f"ran:{'; '.join(commands)}"

    fake_model = GenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "navigate_browser",
                            "args": {"url": "https://example.com/docs"},
                            "id": "tool-nav",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "extract_hyperlinks",
                            "args": {},
                            "id": "tool-links",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "terminal",
                            "args": {"commands": ["pwd", "ls"]},
                            "id": "tool-terminal",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="Browser and shell finished."),
            ],
        ),
    )
    graph = create_agent(
        model=fake_model,
        tools=[navigate_browser, extract_hyperlinks, terminal],
        name="browser-shell",
    )
    adapter = create_acp_agent(
        graph=graph,
        config=AdapterConfig(
            projection_maps=[BrowserProjectionMap(), CommandProjectionMap()],
        ),
    )
    client = RecordingACPClient()
    adapter.on_connect(cast("AcpClient", client))

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.prompt(prompt=[text_block("browse and inspect")], session_id=session.session_id),
    )

    assert response.stop_reason == "end_turn"
    starts = [update for _, update in client.updates if isinstance(update, ToolCallStart)]
    progresses = [update for _, update in client.updates if isinstance(update, ToolCallProgress)]
    assert [update.title for update in starts] == [
        "Navigate https://example.com/docs",
        "Extract hyperlinks",
        "pwd ls",
    ]
    assert len(progresses) == 3
    assert progresses[0].content is not None
    assert isinstance(progresses[0].content[0], ContentToolCallContent)
    assert "status code 200" in progresses[0].content[0].content.text
    assert progresses[1].content is not None
    assert isinstance(progresses[1].content[0], ContentToolCallContent)
    assert "1. https://example.com/docs" in progresses[1].content[0].content.text
    assert progresses[2].content is not None
    assert isinstance(progresses[2].content[0], ContentToolCallContent)
    assert progresses[2].content[0].content.text == "ran:pwd; ls"
    assert agent_message_texts(client) == ["Browser and shell finished."]

    assert starts[0].kind == "fetch"
    assert starts[1].kind == "read"
    assert starts[2].kind == "execute"


def test_langchain_acp_projects_file_management_updates_at_runtime(tmp_path) -> None:
    def move_file(source_path: str, destination_path: str) -> str:
        """Move a file inside the workspace."""
        return f"File moved successfully from {source_path} to {destination_path}."

    fake_model = GenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "move_file",
                            "args": {
                                "source_path": "draft.txt",
                                "destination_path": "archive/draft.txt",
                            },
                            "id": "tool-move",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="Move finished."),
            ],
        ),
    )
    graph = create_agent(model=fake_model, tools=[move_file], name="file-manager")
    adapter = create_acp_agent(
        graph=graph,
        config=AdapterConfig(projection_maps=[CommunityFileManagementProjectionMap()]),
    )
    client = RecordingACPClient()
    adapter.on_connect(cast("AcpClient", client))

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.prompt(prompt=[text_block("archive draft")], session_id=session.session_id),
    )

    assert response.stop_reason == "end_turn"
    tool_start = next(update for _, update in client.updates if isinstance(update, ToolCallStart))
    tool_progress = next(
        update for _, update in client.updates if isinstance(update, ToolCallProgress)
    )
    assert tool_start.kind == "edit"
    assert tool_start.title == "Move `draft.txt` -> `archive/draft.txt`"
    assert tool_progress.content is not None
    assert isinstance(tool_progress.content[0], ContentToolCallContent)
    assert (
        tool_progress.content[0].content.text
        == "File moved successfully from draft.txt to archive/draft.txt."
    )
    assert agent_message_texts(client) == ["Move finished."]


def test_langchain_acp_projects_finance_updates_at_runtime(tmp_path) -> None:
    def google_finance(query: str) -> str:
        """Look up finance results for a query."""
        return "NVDA rises after earnings beat."

    fake_model = GenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "google_finance",
                            "args": {"query": "NVDA"},
                            "id": "tool-finance",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="Finance finished."),
            ],
        ),
    )
    graph = create_agent(model=fake_model, tools=[google_finance], name="finance")
    adapter = create_acp_agent(
        graph=graph,
        config=AdapterConfig(projection_maps=[FinanceProjectionMap()]),
    )
    client = RecordingACPClient()
    adapter.on_connect(cast("AcpClient", client))

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.prompt(prompt=[text_block("check nvda")], session_id=session.session_id),
    )

    assert response.stop_reason == "end_turn"
    tool_start = next(update for _, update in client.updates if isinstance(update, ToolCallStart))
    tool_progress = next(
        update for _, update in client.updates if isinstance(update, ToolCallProgress)
    )
    assert tool_start.kind == "search"
    assert tool_start.title == "Search finance for NVDA"
    assert tool_progress.content is not None
    assert isinstance(tool_progress.content[0], ContentToolCallContent)
    assert tool_progress.content[0].content.text == "NVDA rises after earnings beat."
    assert agent_message_texts(client) == ["Finance finished."]


def test_langchain_acp_bridges_hitl_permissions(tmp_path) -> None:
    def delete_file(path: str) -> str:
        """Delete a file from the workspace."""
        return f"deleted:{path}"

    fake_model = GenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "delete_file",
                            "args": {"path": "draft.txt"},
                            "id": "tool-2",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="Approved."),
            ],
        ),
    )
    middleware = [
        HumanInTheLoopMiddleware(
            interrupt_on={"delete_file": {"allowed_decisions": ["approve", "reject"]}},
        ),
    ]
    graph = create_agent(model=fake_model, tools=[delete_file], middleware=middleware)
    adapter = create_acp_agent(graph=graph, config=AdapterConfig())
    client = RecordingACPClient()
    client.queue_permission_selected("allow_once")
    adapter.on_connect(cast("AcpClient", client))

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.prompt(prompt=[text_block("delete draft.txt")], session_id=session.session_id),
    )

    assert response.stop_reason == "end_turn"
    assert len(client.permission_requests) == 1
    assert client.permission_requests[0][1] == ["allow_once", "reject_once"]
    assert agent_message_texts(client) == ["Approved."]


@dataclass(kw_only=True)
class _PlanState:
    messages: list[Any] = field(default_factory=list)
    todos: list[dict[str, str]] = field(default_factory=list)


def test_langchain_acp_emits_plan_updates_from_graph_state(tmp_path) -> None:
    def planner(state: _PlanState) -> dict[str, Any]:
        return {
            "todos": [
                {"content": "Inspect repo", "status": "completed", "priority": "high"},
                {
                    "content": "Write ACP summary",
                    "status": "in_progress",
                    "priority": "medium",
                },
            ],
        }

    builder = StateGraph(_PlanState)
    builder.add_node("tools", planner)
    builder.add_edge(START, "tools")
    builder.set_finish_point("tools")
    graph = builder.compile()
    adapter = create_acp_agent(graph=graph, config=AdapterConfig())
    client = RecordingACPClient()
    adapter.on_connect(cast("AcpClient", client))

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.prompt(prompt=[text_block("plan this")], session_id=session.session_id),
    )

    assert response.stop_reason == "end_turn"
    plan_update = next(
        cast("AgentPlanUpdate", update)
        for _, update in client.updates
        if isinstance(update, AgentPlanUpdate)
    )
    assert [entry.content for entry in plan_update.entries] == [
        "Inspect repo",
        "Write ACP summary",
    ]


@dataclass(kw_only=True)
class _CustomPlanState:
    messages: list[Any] = field(default_factory=list)
    tasks: list[dict[str, str]] = field(default_factory=list)


def test_langchain_acp_phase5_allows_custom_plan_extraction_bridge(tmp_path) -> None:
    @dataclass(slots=True, kw_only=True)
    class _TasksBridge(CapabilityBridge):
        def extract_plan_entries(self, payload: Any) -> list[PlanEntry] | None:
            if not isinstance(payload, dict):
                return None
            tasks = payload.get("tasks")
            if not isinstance(tasks, list):
                return None
            entries: list[PlanEntry] = []
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                content = task.get("content")
                if not isinstance(content, str):
                    continue
                entries.append(
                    PlanEntry(
                        content=content,
                        status=cast("PlanEntryStatus", task.get("status", "pending")),
                        priority=cast("PlanEntryPriority", task.get("priority", "medium")),
                    ),
                )
            return entries

    tasks_bridge = _TasksBridge()
    assert tasks_bridge.extract_plan_entries("bad-payload") is None
    assert tasks_bridge.extract_plan_entries({"tasks": "bad"}) is None
    assert tasks_bridge.extract_plan_entries({"tasks": ["bad", {"content": 1}]}) == []

    def planner(state: _CustomPlanState) -> dict[str, Any]:
        return {
            "tasks": [
                {"content": "Inspect repo", "status": "completed", "priority": "high"},
                {
                    "content": "Write summary",
                    "status": "in_progress",
                    "priority": "medium",
                },
            ],
        }

    builder = StateGraph(_CustomPlanState)
    builder.add_node("tools", planner)
    builder.add_edge(START, "tools")
    builder.set_finish_point("tools")
    graph = builder.compile()
    adapter = create_acp_agent(
        graph=graph,
        config=AdapterConfig(capability_bridges=[_TasksBridge()]),
    )
    client = RecordingACPClient()
    adapter.on_connect(cast("AcpClient", client))

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.prompt(prompt=[text_block("plan this")], session_id=session.session_id),
    )

    assert response.stop_reason == "end_turn"
    plan_update = next(
        cast("AgentPlanUpdate", update)
        for _, update in client.updates
        if isinstance(update, AgentPlanUpdate)
    )
    assert [entry.content for entry in plan_update.entries] == [
        "Inspect repo",
        "Write summary",
    ]


def test_langchain_acp_phase6_projects_deepagents_execute_updates_at_runtime(
    tmp_path,
) -> None:
    def execute(command: str) -> dict[str, str]:
        """Execute a shell command in the workspace."""
        return {
            "terminal_id": "terminal-1",
            "stdout": f"ran:{command}",
        }

    fake_model = GenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "execute",
                            "args": {"command": "echo hi && sudo rm /tmp/demo"},
                            "id": "tool-exec",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="Command finished."),
            ],
        ),
    )
    graph = create_agent(model=fake_model, tools=[execute], name="deepagents-execute")
    adapter = create_acp_agent(
        graph=graph,
        config=AdapterConfig(projection_maps=[DeepAgentsProjectionMap()]),
    )
    client = RecordingACPClient()
    adapter.on_connect(cast("AcpClient", client))

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.prompt(prompt=[text_block("run the command")], session_id=session.session_id),
    )

    assert response.stop_reason == "end_turn"
    tool_start = next(update for _, update in client.updates if isinstance(update, ToolCallStart))
    tool_progress = next(
        update for _, update in client.updates if isinstance(update, ToolCallProgress)
    )
    assert tool_start.title == "echo hi && sudo rm /tmp/demo"
    assert tool_start.content is not None
    assert len(tool_start.content) == 2
    assert isinstance(tool_start.content[0], ContentToolCallContent)
    assert tool_start.content[0].content.text == "echo hi && sudo rm /tmp/demo"
    assert isinstance(tool_start.content[1], ContentToolCallContent)
    assert tool_start.content[1].content.text == "Potentially risky command"

    assert tool_progress.content is not None
    assert isinstance(tool_progress.content[0], TerminalToolCallContent)
    assert tool_progress.content[0].terminal_id == "terminal-1"
    assert isinstance(tool_progress.content[1], ContentToolCallContent)
    assert tool_progress.content[1].content.text == "ran:echo hi && sudo rm /tmp/demo"
    assert agent_message_texts(client) == ["Command finished."]


def test_langchain_acp_runs_real_deepagents_graph(tmp_path: Path) -> None:
    graph = create_deep_agent(
        model=GenericFakeChatModel(messages=iter([AIMessage(content="DeepAgents ready.")])),
        tools=[],
        name="deepagents-smoke",
    )
    adapter = create_acp_agent(
        graph=graph,
        config=AdapterConfig(
            capability_bridges=[DeepAgentsCompatibilityBridge()],
            projection_maps=[DeepAgentsProjectionMap()],
        ),
    )
    client = RecordingACPClient()
    adapter.on_connect(cast("AcpClient", client))

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.prompt(
            prompt=[text_block("Confirm the integration.")],
            session_id=session.session_id,
        ),
    )

    assert response.stop_reason == "end_turn"
    assert agent_message_texts(client) == ["DeepAgents ready."]


def test_deepagents_projection_matches_builtin_tool_schemas() -> None:
    projection_map = DeepAgentsProjectionMap()

    read_start = projection_map.project_start(
        "read_file",
        raw_input={"file_path": "/workspace/notes.md", "offset": 0, "limit": 20},
    )
    write_start = projection_map.project_start(
        "write_file",
        raw_input={"file_path": "/workspace/report.md", "content": "Ready."},
    )
    search_start = projection_map.project_start(
        "glob",
        raw_input={"pattern": "**/*.py", "path": "/workspace"},
    )
    execute_start = projection_map.project_start(
        "execute",
        raw_input={"command": "pytest -q"},
    )

    assert read_start is not None
    assert read_start.title == "Read `/workspace/notes.md`"
    assert read_start.locations == [ToolCallLocation(path="/workspace/notes.md")]
    assert write_start is not None
    assert write_start.title == "Write `/workspace/report.md`"
    assert write_start.locations == [ToolCallLocation(path="/workspace/report.md")]
    assert search_start is not None
    assert search_start.title == "Glob `**/*.py`"
    assert execute_start is not None
    assert execute_start.title == "pytest -q"


def test_langchain_acp_graph_factory_receives_session_context(tmp_path) -> None:
    captured_session_ids: list[str] = []

    def read_file(path: str) -> str:
        """Read a file from the workspace."""
        return f"ok:{path}"

    assert read_file("demo.txt") == "ok:demo.txt"

    def graph_factory(session) -> Any:
        captured_session_ids.append(session.session_id)
        fake_model = GenericFakeChatModel(
            messages=iter([AIMessage(content="Factory graph ready.")]),
        )
        return create_agent(model=fake_model, tools=[read_file])

    adapter = create_acp_agent(graph_factory=graph_factory, config=AdapterConfig())
    client = RecordingACPClient()
    adapter.on_connect(cast("AcpClient", client))
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    asyncio.run(adapter.prompt(prompt=[text_block("hello")], session_id=session.session_id))

    assert captured_session_ids == [session.session_id, session.session_id]
    assert agent_message_texts(client) == ["Factory graph ready."]


def test_langchain_acp_phase3_rebuilds_graph_from_session_model_and_mode(
    tmp_path,
) -> None:
    captured_builds: list[tuple[str | None, str | None]] = []

    class _SessionModelsProvider:
        def get_model_state(self, session):
            return ModelSelectionState(
                available_models=[
                    AdapterModel(model_id="base", name="Base"),
                    AdapterModel(model_id="gpt-5", name="GPT-5"),
                ],
                current_model_id=session.session_model_id or "base",
                enable_config_option=False,
            )

        def set_model(self, session, model_id: str):
            session.metadata["selected_model"] = model_id
            return ModelSelectionState(
                available_models=[
                    AdapterModel(model_id="base", name="Base"),
                    AdapterModel(model_id="gpt-5", name="GPT-5"),
                ],
                current_model_id=model_id,
                allow_any_model_id=True,
                enable_config_option=False,
            )

    class _SessionModesProvider:
        def get_mode_state(self, session):
            return ModeState(
                modes=[
                    SessionMode(id="ask", name="Ask"),
                    SessionMode(id="plan", name="Plan"),
                ],
                current_mode_id=session.session_mode_id or "ask",
                enable_config_option=False,
            )

        def set_mode(self, session, mode_id: str):
            session.metadata["selected_mode"] = mode_id
            return ModeState(
                modes=[
                    SessionMode(id="ask", name="Ask"),
                    SessionMode(id="plan", name="Plan"),
                ],
                current_mode_id=mode_id,
                enable_config_option=False,
            )

    def graph_factory(session) -> Any:
        model_id = session.session_model_id or "base"
        mode_id = session.session_mode_id or "ask"
        captured_builds.append((model_id, mode_id))
        fake_model = GenericFakeChatModel(
            messages=iter([AIMessage(content=f"{model_id}:{mode_id}")]),
        )
        return create_agent(model=fake_model, tools=[], name=f"{model_id}-{mode_id}")

    adapter = create_acp_agent(
        graph_factory=graph_factory,
        config=AdapterConfig(
            models_provider=_SessionModelsProvider(),
            modes_provider=_SessionModesProvider(),
        ),
    )
    client = RecordingACPClient()
    adapter.on_connect(cast("AcpClient", client))
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    stored_session = adapter._require_session(session.session_id)
    assert stored_session.session_model_id == "base"
    assert session.modes is not None
    assert session.modes.current_mode_id == "ask"

    first = asyncio.run(adapter.prompt(prompt=[text_block("hello")], session_id=session.session_id))
    assert first.stop_reason == "end_turn"
    assert agent_message_texts(client)[-1] == "base:ask"

    asyncio.run(adapter.set_session_model("gpt-5", session_id=session.session_id))
    asyncio.run(adapter.set_session_mode(session.session_id, "plan"))
    second = asyncio.run(
        adapter.prompt(prompt=[text_block("again")], session_id=session.session_id),
    )
    assert second.stop_reason == "end_turn"
    assert agent_message_texts(client)[-1] == "gpt-5:plan"
    assert captured_builds == [("base", "ask"), ("base", "ask"), ("gpt-5", "plan")]


@dataclass(slots=True, kw_only=True)
class _StructuredPlanGraph:
    chunks: list[tuple[tuple[Any, ...], str, Any]]

    async def astream(
        self,
        stream_input: Any,
        *,
        config: Any,
        stream_mode: Any,
        subgraphs: bool,
    ):
        del stream_input, config, stream_mode, subgraphs
        for chunk in self.chunks:
            yield chunk


@dataclass(slots=True, kw_only=True)
class _RecordingGraph:
    chunks: list[tuple[tuple[Any, ...], str, Any]] = field(default_factory=list)
    inputs: list[Any] = field(default_factory=list)

    async def astream(
        self,
        stream_input: Any,
        *,
        config: Any,
        stream_mode: Any,
        subgraphs: bool,
    ):
        del config, stream_mode, subgraphs
        self.inputs.append(stream_input)
        for chunk in self.chunks:
            yield chunk


def test_langchain_acp_phase4_structured_plan_output_persists_task_plan(
    tmp_path: Path,
) -> None:
    graph = _StructuredPlanGraph(
        chunks=[
            (
                (),
                "updates",
                {
                    "generate_structured_response": {
                        "structured_response": {
                            "plan_md": "# Investigation plan",
                            "plan_entries": [
                                {
                                    "content": "Inspect repo",
                                    "status": "pending",
                                    "priority": "high",
                                },
                                {
                                    "content": "Write summary",
                                    "status": "in_progress",
                                    "priority": "medium",
                                },
                            ],
                        },
                    },
                },
            ),
        ],
    )
    adapter = cast(
        "Any",
        create_acp_agent(
            graph=cast("Any", graph),
            config=AdapterConfig(
                available_modes=[SessionMode(id="plan", name="Plan")],
                default_mode_id="plan",
                plan_mode_id="plan",
            ),
        ),
    )
    client = RecordingACPClient()
    adapter.on_connect(cast("AcpClient", client))
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    response = asyncio.run(
        adapter.prompt(prompt=[text_block("plan the work")], session_id=session.session_id),
    )

    assert response.stop_reason == "end_turn"
    stored_session = adapter._require_session(session.session_id)
    assert stored_session.plan_markdown == "# Investigation plan"
    assert [entry["content"] for entry in stored_session.plan_entries] == [
        "Inspect repo",
        "Write summary",
    ]
    plan_update = next(
        cast("AgentPlanUpdate", update)
        for _, update in client.updates
        if isinstance(update, AgentPlanUpdate)
    )
    assert [entry.content for entry in plan_update.entries] == [
        "Inspect repo",
        "Write summary",
    ]


def test_langchain_acp_phase4_tool_based_plan_generation_updates_plan_state(
    tmp_path: Path,
) -> None:
    fake_model = GenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "acp_set_plan",
                            "args": {
                                "entries": [
                                    {
                                        "content": "Book flights",
                                        "status": "pending",
                                        "priority": "high",
                                    },
                                ],
                                "plan_md": "# Travel booking",
                            },
                            "id": "tool-1",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "acp_mark_plan_done",
                            "args": {"index": 1},
                            "id": "tool-2",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="Trip plan saved."),
            ],
        ),
    )
    graph = create_agent(
        model=fake_model,
        tools=cast("list[Callable[..., Any]]", list(native_plan_tools())),
        name="planner",
    )
    adapter = cast(
        "Any",
        create_acp_agent(
            graph=graph,
            config=AdapterConfig(
                available_modes=[SessionMode(id="plan", name="Plan")],
                default_mode_id="plan",
                default_plan_generation_type="tools",
                plan_mode_id="plan",
            ),
        ),
    )
    client = RecordingACPClient()
    adapter.on_connect(cast("AcpClient", client))
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    response = asyncio.run(
        adapter.prompt(prompt=[text_block("save a plan")], session_id=session.session_id),
    )

    assert response.stop_reason == "end_turn"
    stored_session = adapter._require_session(session.session_id)
    assert stored_session.plan_markdown == "# Travel booking"
    assert stored_session.plan_entries == [
        {
            "content": "Book flights",
            "status": "completed",
            "priority": "high",
        },
    ]
    plan_updates = [
        cast("AgentPlanUpdate", update)
        for _, update in client.updates
        if isinstance(update, AgentPlanUpdate)
    ]
    assert len(plan_updates) == 2
    assert plan_updates[-1].entries == [
        PlanEntry(content="Book flights", status="completed", priority="high"),
    ]
    assert agent_message_texts(client)[-1] == "Trip plan saved."


def test_langchain_acp_phase6_projects_structured_runtime_events(
    tmp_path: Path,
) -> None:
    graph = _RecordingGraph(
        chunks=[
            (
                (),
                "updates",
                {
                    "callback_events": [
                        {
                            "type": "tool_call",
                            "toolCallId": "event-1",
                            "title": "run shell",
                            "kind": "execute",
                            "status": "in_progress",
                        },
                        {
                            "session_update": "tool_call_update",
                            "toolCallId": "event-1",
                            "status": "completed",
                            "content": "shell complete",
                        },
                    ],
                },
            ),
        ],
    )
    adapter = create_acp_agent(
        graph=cast("Any", graph),
        config=AdapterConfig(event_projection_maps=[StructuredEventProjectionMap()]),
    )
    client = RecordingACPClient()
    adapter.on_connect(cast("AcpClient", client))
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    response = asyncio.run(
        adapter.prompt(prompt=[text_block("run diagnostics")], session_id=session.session_id),
    )

    assert response.stop_reason == "end_turn"
    projected_updates = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallStart | ToolCallProgress)
    ]
    assert len(projected_updates) == 2
    assert cast("ToolCallStart", projected_updates[0]).title == "run shell"
    progress = cast("ToolCallProgress", projected_updates[1])
    assert progress.status == "completed"
    assert progress.content is not None
    assert cast("Any", progress.content[0]).content.text == "shell complete"


def test_langchain_acp_phase6_preserves_multimodal_and_resource_prompt_blocks(
    tmp_path: Path,
) -> None:
    graph = _RecordingGraph()
    adapter = create_acp_agent(graph=cast("Any", graph), config=AdapterConfig())
    client = RecordingACPClient()
    adapter.on_connect(cast("AcpClient", client))
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    response = asyncio.run(
        adapter.prompt(
            prompt=[
                text_block("Plan the trip"),
                AudioContentBlock(type="audio", data="d2F2", mime_type="audio/wav"),
                ResourceContentBlock(
                    type="resource_link",
                    name="travel.md",
                    uri="file:///travel.md",
                    description="Trip notes",
                ),
                EmbeddedResourceContentBlock(
                    type="resource",
                    resource=TextResourceContents(
                        uri="file:///notes.txt",
                        text="Window seat preferred",
                        mime_type="text/plain",
                    ),
                ),
                EmbeddedResourceContentBlock(
                    type="resource",
                    resource=BlobResourceContents(
                        uri="file:///boarding.png",
                        blob="aGVsbG8=",
                        mime_type="image/png",
                    ),
                ),
            ],
            session_id=session.session_id,
        ),
    )

    assert response.stop_reason == "end_turn"
    recorded_content = graph.inputs[0]["messages"][0]["content"]
    assert recorded_content[0] == {"type": "text", "text": "Plan the trip"}
    assert recorded_content[1] == {
        "type": "audio",
        "base64": "d2F2",
        "mime_type": "audio/wav",
    }
    assert "Trip notes" in recorded_content[2]["text"]
    assert recorded_content[3]["text"].endswith("Window seat preferred")
    assert recorded_content[4]["type"] == "image_url"
