from __future__ import annotations as _annotations

import asyncio
import sys
import types
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic_acp.runtime.slash_commands import (
    _iter_mcp_server_infos,
    _mcp_server_info_from_bridge_metadata,
    _mcp_server_info_from_http_toolset,
    _mcp_server_info_from_session_payload,
    _mcp_server_info_from_stdio_toolset,
    _toolset_name,
    extract_session_mcp_servers,
    list_agent_mcp_servers,
    list_agent_tools,
    parse_slash_command,
    render_hook_listing,
    render_mcp_server_listing,
    render_mode_message,
    render_model_message,
    render_thinking_message,
    render_tool_listing,
    validate_mode_command_ids,
)
from pydantic_ai import ModelRequest, ModelResponse, TextPart
from pydantic_ai.capabilities import Hooks
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.tools import DeferredToolRequests, Tool
from pydantic_ai.toolsets._dynamic import DynamicToolset
from pydantic_ai.toolsets.combined import CombinedToolset
from pydantic_ai.toolsets.wrapper import WrapperToolset
from typing_extensions import Sentinel

from .support import (
    UTC,
    AcpSessionContext,
    AdapterConfig,
    Agent,
    AvailableCommandsUpdate,
    ConfigOptionUpdate,
    CurrentModeUpdate,
    JsonValue,
    MemorySessionStore,
    Path,
    PrepareToolsBridge,
    PrepareToolsMode,
    RecordingClient,
    TestModel,
    ThinkingBridge,
    ToolCallProgress,
    agent_message_texts,
    create_acp_agent,
    datetime,
    text_block,
)

_INVALID_SLASH_VALUE = Sentinel("_INVALID_SLASH_VALUE")


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
    available_model_ids = [model.model_id for model in response.models.available_models]
    assert "openai:gpt-5-mini" in available_model_ids
    assert "codex:gpt-5.4-mini" in available_model_ids
    assert "codex:gpt-5-mini" not in available_model_ids
    assert response.config_options is not None
    assert response.config_options[0].id == "model"


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


def test_slash_command_render_helpers_cover_empty_states() -> None:
    assert parse_slash_command("hello") is None
    assert parse_slash_command("/") is None
    parsed_unknown = parse_slash_command("/unknown arg")
    assert parsed_unknown is not None
    assert parsed_unknown.name == "unknown"
    assert parsed_unknown.argument == "arg"
    parsed = parse_slash_command(" /MODEL codex:gpt-5.4 ")
    assert parsed is not None
    assert parsed.name == "model"
    assert parsed.argument == "codex:gpt-5.4"
    parsed_mode = parse_slash_command("/PLAN")
    assert parsed_mode is not None
    assert parsed_mode.name == "plan"
    assert parsed_mode.argument is None
    parsed_thinking = parse_slash_command("/THINKING high")
    assert parsed_thinking is not None
    assert parsed_thinking.name == "thinking"
    assert parsed_thinking.argument == "high"
    assert render_mode_message(None) == "Current mode: unavailable"
    assert render_model_message(None) == "Current model: unavailable"
    assert render_thinking_message(None) == "Thinking effort: unavailable"
    assert render_tool_listing([]) == "No tools are currently registered."
    assert (
        render_hook_listing([], projection_map=None)
        == "No Hooks capability callbacks are currently registered."
    )
    assert render_mcp_server_listing([]) == "No MCP servers are currently attached."


def test_validate_mode_command_ids_rejects_invalid_duplicate_and_reserved_values() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        validate_mode_command_ids(["   "])

    with pytest.raises(ValueError, match="cannot contain whitespace"):
        validate_mode_command_ids(["code review"])

    with pytest.raises(ValueError, match="Duplicate ids: review"):
        validate_mode_command_ids(["review", " Review "])

    with pytest.raises(ValueError, match="reserved slash command names"):
        validate_mode_command_ids(["model"])


def test_list_agent_tools_skips_internal_invalid_and_non_tool_entries() -> None:
    agent = Agent(TestModel(custom_output_text="unused"))
    function_toolset = agent._function_toolset
    cast(Any, function_toolset).tools = {
        "acp_hidden": Tool(lambda: "x", takes_ctx=False, name="acp_hidden"),
        1: Tool(lambda: "x", takes_ctx=False, name="bad"),  # type: ignore[dict-item]
        "plain": _INVALID_SLASH_VALUE,
        "visible": Tool(lambda: "x", takes_ctx=False, name="visible"),
    }

    tool_infos = list_agent_tools(agent)

    assert [tool_info.name for tool_info in tool_infos] == ["visible"]
    assert list_agent_tools(cast(Any, SimpleNamespace())) == []


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


def test_mode_slash_commands_switch_modes_and_emit_ui_updates(tmp_path: Path) -> None:
    def passthrough_tools(
        tool_defs: list[Any],
    ) -> list[Any]:
        return list(tool_defs)

    adapter = create_acp_agent(
        agent=Agent(TestModel(model_name="openai:gpt-5-mini", custom_output_text="ok")),
        config=AdapterConfig(
            capability_bridges=[
                PrepareToolsBridge(
                    default_mode_id="ask",
                    modes=[
                        PrepareToolsMode(
                            id="ask",
                            name="Ask",
                            description="Ask mode.",
                            prepare_func=lambda _ctx, tool_defs: passthrough_tools(tool_defs),
                        ),
                        PrepareToolsMode(
                            id="plan",
                            name="Plan",
                            description="Plan mode.",
                            plan_mode=True,
                            prepare_func=lambda _ctx, tool_defs: passthrough_tools(tool_defs),
                        ),
                        PrepareToolsMode(
                            id="agent",
                            name="Agent",
                            description="Agent mode.",
                            plan_tools=True,
                            prepare_func=lambda _ctx, tool_defs: passthrough_tools(tool_defs),
                        ),
                    ],
                )
            ],
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    client.updates.clear()

    asyncio.run(adapter.prompt(prompt=[text_block("/plan")], session_id=session.session_id))
    asyncio.run(adapter.prompt(prompt=[text_block("/agent")], session_id=session.session_id))
    asyncio.run(adapter.prompt(prompt=[text_block("/ask")], session_id=session.session_id))

    assert agent_message_texts(client) == [
        "Current mode: plan",
        "Current mode: agent",
        "Current mode: ask",
    ]
    mode_updates = [update for _, update in client.updates if isinstance(update, CurrentModeUpdate)]
    assert [update.current_mode_id for update in mode_updates] == [
        "plan",
        "agent",
        "ask",
    ]
    config_updates = [
        update for _, update in client.updates if isinstance(update, ConfigOptionUpdate)
    ]
    assert [
        next(option.current_value for option in update.config_options if option.id == "mode")
        for update in config_updates
    ] == [
        "plan",
        "agent",
        "ask",
    ]


def test_available_commands_are_derived_from_configured_modes(tmp_path: Path) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(model_name="openai:gpt-5-mini", custom_output_text="ok")),
        config=AdapterConfig(
            capability_bridges=[
                PrepareToolsBridge(
                    default_mode_id="review",
                    modes=[
                        PrepareToolsMode(
                            id="review",
                            name="Review",
                            description="Review mode.",
                            prepare_func=lambda _ctx, tool_defs: list(tool_defs),
                        ),
                        PrepareToolsMode(
                            id="execute",
                            name="Execute",
                            description="Execution mode.",
                            prepare_func=lambda _ctx, tool_defs: list(tool_defs),
                        ),
                    ],
                ),
                ThinkingBridge(),
            ],
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    command_updates = [
        update for _, update in client.updates if isinstance(update, AvailableCommandsUpdate)
    ]

    assert len(command_updates) == 1
    assert [command.name for command in command_updates[0].available_commands] == [
        "review",
        "execute",
        "model",
        "thinking",
        "tools",
        "hooks",
        "mcp-servers",
    ]


def test_reserved_mode_command_ids_raise_value_error() -> None:
    with pytest.raises(
        ValueError,
        match="reserved slash command names",
    ):
        validate_mode_command_ids(["model"])


def test_thinking_slash_command_updates_ui_state_and_session_config(
    tmp_path: Path,
) -> None:
    observed_model_settings: list[Any] = []

    def route_with_thinking(
        messages: list[ModelRequest | ModelResponse],
        info: AgentInfo,
    ) -> ModelResponse:
        del messages
        observed_model_settings.append(info.model_settings)
        return ModelResponse(parts=[TextPart("ok")])

    adapter = create_acp_agent(
        agent=Agent(
            FunctionModel(route_with_thinking, model_name="thinking-model"),
            output_type=str,
        ),
        config=AdapterConfig(
            capability_bridges=[ThinkingBridge()],
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    client.updates.clear()

    asyncio.run(adapter.prompt(prompt=[text_block("/thinking")], session_id=session.session_id))
    asyncio.run(
        adapter.prompt(prompt=[text_block("/thinking HIGH")], session_id=session.session_id)
    )
    asyncio.run(adapter.prompt(prompt=[text_block("/thinking")], session_id=session.session_id))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Use the configured thinking effort.")],
            session_id=session.session_id,
        )
    )

    assert agent_message_texts(client) == [
        "Thinking effort: default",
        "Thinking effort: high",
        "Thinking effort: high",
        "ok",
    ]
    config_updates = [
        update for _, update in client.updates if isinstance(update, ConfigOptionUpdate)
    ]
    thinking_values = [
        next(option.current_value for option in update.config_options if option.id == "thinking")
        for update in config_updates
        if any(option.id == "thinking" for option in update.config_options)
    ]
    assert thinking_values
    assert all(value == "high" for value in thinking_values)
    assert observed_model_settings[-1] == {"thinking": "high"}


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


def test_extract_session_mcp_servers_skips_invalid_and_dedupes_sources() -> None:
    session = AcpSessionContext(
        session_id="slash-session",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        mcp_servers=[
            {
                "name": "repo-http",
                "transport": "http",
                "url": "https://repo.example/mcp",
            },
            {"transport": "stdio", "command": "python"},
            {
                "name": "repo-stdio",
                "transport": "stdio",
                "command": "python",
                "args": ["a.py"],
            },
        ],
        metadata=cast(
            dict[str, JsonValue],
            {
                "pydantic_acp": {
                    "mcp": {
                        "servers": [
                            {
                                "name": "repo-http",
                                "transport": "http",
                                "url": "https://repo.example/mcp",
                            },
                            {
                                "name": "bridge-only",
                                "transport": "sse",
                                "tool_prefix": "docs",
                                "description": "remote docs",
                            },
                            {1: "invalid"},
                        ]
                    }
                }
            },
        ),
    )

    server_infos = extract_session_mcp_servers(session)

    assert [(info.name, info.transport, info.target, info.source) for info in server_infos] == [
        ("repo-http", "http", "https://repo.example/mcp", "session"),
        ("repo-stdio", "stdio", "python a.py", "session"),
        ("bridge-only", "sse", "docs | remote docs", "bridge"),
    ]


def test_extract_session_mcp_servers_covers_agent_dedupes_and_invalid_metadata_shapes() -> None:
    http_cls = type("MCPServerStreamableHTTP", (), {})
    http_cls.__module__ = "pydantic_ai.mcp"
    http_toolset = http_cls()
    cast(Any, http_toolset).url = "https://repo.example/mcp"
    cast(Any, http_toolset).id = "repo-http"

    agent = cast(Any, SimpleNamespace(toolsets=[http_toolset, http_toolset]))
    expected_agent_infos = list_agent_mcp_servers(agent)
    assert len(expected_agent_infos) == 1

    bare_session = AcpSessionContext(
        session_id="slash-invalid-root",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        metadata={"pydantic_acp": "bad"},  # type: ignore[dict-item]
    )
    assert extract_session_mcp_servers(bare_session, agent=agent) == expected_agent_infos

    session_bad_mcp = AcpSessionContext(
        session_id="slash-invalid-mcp",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        metadata={"pydantic_acp": {"mcp": "bad"}},  # type: ignore[dict-item]
    )
    infos = extract_session_mcp_servers(session_bad_mcp, agent=agent)
    assert len(infos) == 1
    assert infos[0].name == "repo-http"

    session_bad_servers = AcpSessionContext(
        session_id="slash-invalid-servers",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        metadata={"pydantic_acp": {"mcp": {"servers": "bad"}}},  # type: ignore[dict-item]
    )
    infos = extract_session_mcp_servers(session_bad_servers, agent=agent)
    assert len(infos) == 1
    assert infos[0].target == "https://repo.example/mcp"

    session_with_duplicates = AcpSessionContext(
        session_id="slash-dupes",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        metadata={
            "pydantic_acp": {
                "mcp": {
                    "servers": [
                        {
                            "name": "repo-http",
                            "transport": "http",
                            "url": "https://repo.example/mcp",
                        }
                    ]
                }
            }
        },
    )
    infos = extract_session_mcp_servers(session_with_duplicates, agent=agent)
    assert [(info.name, info.source) for info in infos] == [("repo-http", "agent")]


def test_mcp_helper_parsers_cover_invalid_and_fallback_paths() -> None:
    assert _mcp_server_info_from_session_payload({"transport": "stdio"}) is None
    assert _mcp_server_info_from_session_payload({"name": "demo"}) is None
    stdio_info = _mcp_server_info_from_session_payload(
        {"name": "stdio", "transport": "stdio", "args": ["--serve"]}
    )
    assert stdio_info is not None
    assert stdio_info.target == "<stdio> --serve"
    http_info = _mcp_server_info_from_session_payload(
        {"name": "http", "transport": "http", "url": "https://demo.test/mcp"}
    )
    assert http_info is not None
    assert http_info.target == "https://demo.test/mcp"

    assert _mcp_server_info_from_bridge_metadata("bad") is None
    assert _mcp_server_info_from_bridge_metadata({"name": "demo"}) is None
    bridge_info = _mcp_server_info_from_bridge_metadata({"name": "demo", "transport": "http"})
    assert bridge_info is not None
    assert bridge_info.target == "<http>"

    stdio_cls = type("MCPServerStdio", (), {})
    stdio_cls.__module__ = "pydantic_ai.mcp"
    stdio_toolset = stdio_cls()
    cast(Any, stdio_toolset).command = ""
    cast(Any, stdio_toolset).args = []
    assert _mcp_server_info_from_stdio_toolset(stdio_toolset) is None
    cast(Any, stdio_toolset).command = "python"
    cast(Any, stdio_toolset).tool_prefix = None
    stdio_result = _mcp_server_info_from_stdio_toolset(stdio_toolset)
    assert stdio_result is not None
    assert stdio_result.target == "python"

    http_cls = type("MCPServerStreamableHTTP", (), {})
    http_cls.__module__ = "pydantic_ai.mcp"
    http_toolset = http_cls()
    cast(Any, http_toolset).url = "https://demo.test/mcp"
    cast(Any, http_toolset).tool_prefix = "repo."
    cast(Any, http_toolset)._id = "toolset-id"
    http_result = _mcp_server_info_from_http_toolset(http_toolset)
    assert http_result is not None
    assert http_result.target == "https://demo.test/mcp | prefix=repo."
    cast(Any, http_toolset).url = ""
    assert _mcp_server_info_from_http_toolset(http_toolset) is None
    assert _toolset_name(SimpleNamespace(), fallback="fallback") == "fallback"


def test_list_agent_mcp_servers_handles_fake_toolsets_and_nested_wrappers() -> None:
    stdio_cls = type("MCPServerStdio", (), {})
    stdio_cls.__module__ = "pydantic_ai.mcp"
    stdio_toolset = stdio_cls()
    cast(Any, stdio_toolset).command = "python"
    cast(Any, stdio_toolset).args = ["server.py"]
    cast(Any, stdio_toolset).tool_prefix = "fs"
    cast(Any, stdio_toolset).id = "local-stdio"

    http_cls = type("MCPServerStreamableHTTP", (), {})
    http_cls.__module__ = "pydantic_ai.mcp"
    http_toolset = http_cls()
    cast(Any, http_toolset).url = "https://example.com/mcp"
    cast(Any, http_toolset).tool_prefix = "docs"
    cast(Any, http_toolset)._id = "remote-http"

    dummy_agent = types.SimpleNamespace(toolsets=[stdio_toolset, http_toolset])
    server_infos = list_agent_mcp_servers(cast(Any, dummy_agent))

    assert [(info.name, info.transport, info.target) for info in server_infos] == [
        ("local-stdio", "stdio", "python server.py | prefix=fs"),
        ("remote-http", "http", "https://example.com/mcp | prefix=docs"),
    ]

    dup_agent = types.SimpleNamespace(toolsets=[stdio_toolset, stdio_toolset])
    deduped = list_agent_mcp_servers(cast(Any, dup_agent))
    assert [(info.name, info.target) for info in deduped] == [
        ("local-stdio", "python server.py | prefix=fs")
    ]

    assert _iter_mcp_server_infos(CombinedToolset([])) == []
    assert _iter_mcp_server_infos(WrapperToolset(CombinedToolset([]))) == []
    assert _iter_mcp_server_infos(DynamicToolset(lambda _ctx: None)) == []
    dynamic_toolset = DynamicToolset(lambda _ctx: None)
    dynamic_toolset._toolset = cast(Any, http_toolset)
    dynamic_infos = _iter_mcp_server_infos(dynamic_toolset)
    assert [(info.name, info.transport) for info in dynamic_infos] == [("remote-http", "http")]


def test_extract_session_mcp_servers_dedupes_agent_and_session_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    duplicate_agent_infos = [
        SimpleNamespace(
            name="docs",
            transport="http",
            target="https://demo.test/mcp",
            source="agent",
        ),
        SimpleNamespace(
            name="docs",
            transport="http",
            target="https://demo.test/mcp",
            source="agent",
        ),
    ]
    monkeypatch.setattr(
        "pydantic_acp.runtime.slash_commands.list_agent_mcp_servers",
        lambda agent: duplicate_agent_infos,
    )
    session = AcpSessionContext(
        session_id="slash-agent-dedupes",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        mcp_servers=[
            {"name": "repo", "transport": "http", "url": "https://repo.example/mcp"},
            {"name": "repo", "transport": "http", "url": "https://repo.example/mcp"},
        ],
    )

    infos = extract_session_mcp_servers(session, agent=cast(Any, _INVALID_SLASH_VALUE))

    assert [(info.name, info.source) for info in infos] == [
        ("docs", "agent"),
        ("repo", "session"),
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
