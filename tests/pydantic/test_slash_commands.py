from __future__ import annotations as _annotations

import asyncio
import sys
import types

import pytest
from pydantic_ai.capabilities import Hooks
from pydantic_ai.tools import DeferredToolRequests

from .support import (
    AdapterConfig,
    Agent,
    AvailableCommandsUpdate,
    MemorySessionStore,
    Path,
    RecordingClient,
    TestModel,
    ToolCallProgress,
    agent_message_texts,
    create_acp_agent,
    text_block,
)


def test_new_session_exposes_current_model_without_explicit_model_selection(
    tmp_path: Path,
) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(model_name="openai:gpt-5-mini", custom_output_text="ok")),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )

    response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    assert response.models is not None
    assert response.models.current_model_id == "openai:gpt-5-mini"
    assert [model.model_id for model in response.models.available_models] == ["openai:gpt-5-mini"]


def test_new_session_emits_available_commands_update(tmp_path: Path) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(model_name="openai:gpt-5-mini", custom_output_text="ok")),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    command_updates = [
        update for _, update in client.updates if isinstance(update, AvailableCommandsUpdate)
    ]

    assert len(command_updates) == 1
    assert [command.name for command in command_updates[0].available_commands] == [
        "model",
        "tools",
        "hooks",
        "mcp-servers",
    ]


def test_model_slash_command_reports_current_model_and_sets_new_model(
    tmp_path: Path,
) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(model_name="openai:gpt-5-mini", custom_output_text="ok")),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(adapter.prompt(prompt=[text_block("/model")], session_id=session.session_id))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("/model openai:gpt-5")],
            session_id=session.session_id,
        )
    )
    asyncio.run(adapter.prompt(prompt=[text_block("/model")], session_id=session.session_id))

    assert agent_message_texts(client) == [
        "Current model: openai:gpt-5-mini",
        "Current model: openai:gpt-5",
        "Current model: openai:gpt-5",
    ]


def test_model_slash_command_accepts_codex_models(tmp_path: Path, monkeypatch) -> None:
    session_store = MemorySessionStore()
    adapter = create_acp_agent(
        agent=Agent(TestModel(model_name="openai:gpt-5-mini", custom_output_text="ok")),
        config=AdapterConfig(session_store=session_store),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    fake_module = types.ModuleType("codex_auth_helper")
    fake_model = TestModel(model_name="gpt-5", custom_output_text="codex")

    def create_codex_responses_model(model_id: str) -> TestModel:
        assert model_id == "gpt-5"
        return fake_model

    fake_module.__dict__["create_codex_responses_model"] = create_codex_responses_model
    monkeypatch.setitem(sys.modules, "codex_auth_helper", fake_module)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("/model codex:gpt-5")],
            session_id=session.session_id,
        )
    )
    stored_session = session_store.get(session.session_id)

    assert stored_session is not None
    assert stored_session.session_model_id == "codex:gpt-5"


def test_invalid_selected_model_falls_back_to_default_model(tmp_path: Path) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(model_name="openai:gpt-5-mini", custom_output_text="default")),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("/model broken:model")],
            session_id=session.session_id,
        )
    )
    client.updates.clear()

    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Run after invalid model selection.")],
            session_id=session.session_id,
        )
    )
    asyncio.run(adapter.prompt(prompt=[text_block("/model")], session_id=session.session_id))

    assert agent_message_texts(client) == [
        "default",
        "Current model: openai:gpt-5-mini",
    ]


def test_tools_slash_command_lists_registered_tools(tmp_path: Path) -> None:
    agent = Agent(TestModel(custom_output_text="unused"), output_type=[str, DeferredToolRequests])

    @agent.tool_plain(docstring_format="google")
    def read_repo(path: str) -> str:
        """Read a repository file.

        Args:
            path: Relative path to read.
        """

        return path

    @agent.tool_plain(requires_approval=True)
    def delete_repo(path: str) -> str:
        return path

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(adapter.prompt(prompt=[text_block("/tools")], session_id=session.session_id))

    assert agent_message_texts(client) == [
        "Available tools:\n- delete_repo [approval]\n- read_repo: Read a repository file."
    ]


def test_hooks_slash_command_lists_registered_hooks(tmp_path: Path) -> None:
    hooks = Hooks[None]()

    @hooks.on.before_model_request
    async def annotate_request(ctx, request_context):
        del ctx
        return request_context

    @hooks.on.before_tool_execute(tools=["echo"])
    async def audit_echo(ctx, *, call, tool_def, args):
        del ctx, call, tool_def
        return args

    agent = Agent(TestModel(custom_output_text="unused"), capabilities=[hooks])

    @agent.tool_plain
    def echo(text: str) -> str:
        return text

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(adapter.prompt(prompt=[text_block("/hooks")], session_id=session.session_id))

    assert agent_message_texts(client) == [
        "Registered hooks:\n"
        "- Before Model: annotate_request\n"
        "- Before Tool: audit_echo [tools: echo]"
    ]


def test_mcp_servers_slash_command_extracts_servers_from_agent_toolsets(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mcp", exc_type=ImportError)
    from pydantic_ai.capabilities import MCP
    from pydantic_ai.mcp import MCPServerSSE, MCPServerStdio

    agent = Agent(
        TestModel(custom_output_text="unused"),
        capabilities=[MCP("https://example.com/mcp", id="cap-http")],
        toolsets=[
            MCPServerSSE("https://example.com/sse", id="remote-sse", tool_prefix="docs"),
            MCPServerStdio("python", args=["server.py"], id="local-stdio", tool_prefix="fs"),
        ],
    )
    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("/mcp-servers")],
            session_id=session.session_id,
        )
    )

    assert agent_message_texts(client) == [
        "MCP servers:\n"
        "- remote-sse (sse, agent): https://example.com/sse | prefix=docs\n"
        "- local-stdio (stdio, agent): python server.py | prefix=fs\n"
        "- https://example.com/mcp (http, agent): https://example.com/mcp"
    ]


def test_invalid_selected_model_does_not_leave_failed_tool_updates(
    tmp_path: Path,
) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(model_name="openai:gpt-5-mini", custom_output_text="default")),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("/model broken:model")],
            session_id=session.session_id,
        )
    )
    client.updates.clear()

    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Run after invalid model selection.")],
            session_id=session.session_id,
        )
    )

    tool_failures = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallProgress) and update.status == "failed"
    ]
    assert tool_failures == []
