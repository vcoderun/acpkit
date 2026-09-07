from __future__ import annotations as _annotations

import builtins
from types import SimpleNamespace
from typing import Any, cast

import pydantic_acp.bridges.mcp as mcp_module
import pytest

from .support import (
    UTC,
    AcpSessionContext,
    AdapterConfig,
    Agent,
    AgentBridgeBuilder,
    ApprovalRequired,
    McpBridge,
    McpServerDefinition,
    McpToolDefinition,
    MemorySessionStore,
    NativeApprovalBridge,
    Path,
    RecordingClient,
    RunContext,
    SessionConfigOptionBoolean,
    SessionConfigOptionSelect,
    SessionConfigSelectGroup,
    SessionConfigSelectOption,
    SessionInfoUpdate,
    SessionMcpBridge,
    TestModel,
    create_acp_agent,
    datetime,
    text_block,
)


def test_mcp_bridge_handles_empty_and_prefix_scoped_behaviour() -> None:
    session = AcpSessionContext(
        session_id="session-mcp",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    agent = Agent(TestModel(custom_output_text="unused"))

    empty_bridge = McpBridge()
    assert empty_bridge.get_mcp_capabilities() is None
    assert empty_bridge.get_session_metadata(session, agent) is None
    assert empty_bridge.get_config_options(session, agent) is None
    assert empty_bridge.get_tool_kind("missing") is None
    assert empty_bridge.get_approval_policy_key("missing") is None

    bridge = McpBridge(
        approval_policy_scope="prefix",
        servers=[
            McpServerDefinition(
                server_id="repo",
                name="Repo",
                transport="http",
                url="https://repo.example/mcp",
                tool_prefix="mcp.repo.",
            ),
        ],
        tools=[
            McpToolDefinition(
                tool_name="mcp.explicit.search",
                server_id="repo",
                kind="search",
            ),
        ],
        config_options=[
            SessionConfigOptionBoolean(
                id="mcp_enabled",
                name="MCP Enabled",
                current_value=False,
                type="boolean",
            ),
            SessionConfigOptionSelect(
                id="mcp_scope",
                name="MCP Scope",
                current_value="repo",
                options=[
                    SessionConfigSelectOption(name="Repo", value="repo"),
                    SessionConfigSelectOption(name="Docs", value="docs"),
                ],
                type="select",
            ),
        ],
    )

    capabilities = bridge.get_mcp_capabilities()
    assert capabilities is not None
    assert capabilities.http is True
    assert bridge.get_tool_kind("mcp.explicit.search") == "search"
    assert bridge.get_tool_kind("mcp.repo.read_file") == "execute"
    assert bridge.get_approval_policy_key("mcp.explicit.search") == "mcp:prefix:mcp.repo."
    assert bridge.get_approval_policy_key("mcp.repo.read_file") == "mcp:prefix:mcp.repo."
    assert bridge.get_approval_policy_key("missing.tool") is None

    assert bridge.set_config_option(session, agent, "mcp_enabled", "yes") is None
    assert bridge.set_config_option(session, agent, "mcp_scope", True) is None
    assert bridge.set_config_option(session, agent, "mcp_scope", "invalid") is None

    updated_options = bridge.set_config_option(session, agent, "mcp_enabled", True)
    assert updated_options is not None
    updated_options = bridge.set_config_option(session, agent, "mcp_scope", "docs")
    assert updated_options is not None

    metadata = bridge.get_session_metadata(session, agent)
    assert metadata is not None
    assert metadata["approval_policy_scope"] == "prefix"
    assert metadata["config_option_ids"] == ["mcp_enabled", "mcp_scope"]
    assert metadata["config"] == {"mcp_enabled": True, "mcp_scope": "docs"}


@pytest.mark.asyncio
async def test_session_mcp_bridge_builds_toolset_from_session_servers() -> None:
    session = AcpSessionContext(
        session_id="session-mcp-toolset",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        mcp_servers=[
            {
                "headers": {"Authorization": "Bearer secret"},
                "name": "repo",
                "transport": "http",
                "url": "https://repo.example/mcp",
            },
            {
                "args": ["server.py"],
                "command": "python",
                "env": {"TOKEN": "secret"},
                "name": "repo",
                "transport": "stdio",
            },
        ],
    )
    bridge = SessionMcpBridge(
        include_return_schema=True,
        tool_name_prefixes=frozenset({"repo_"}),
    )

    capabilities = bridge.build_agent_capabilities(session)

    assert len(capabilities) == 1
    assert bridge.get_tool_kind("repo_search") == "execute"
    assert bridge.get_tool_kind("search") is None
    assert bridge.get_mcp_capabilities() is not None
    assert (
        SessionMcpBridge(advertise_http=False, advertise_sse=False).get_mcp_capabilities() is None
    )

    capability = cast(
        "Any",
        await capabilities[0].for_run(cast("Any", SimpleNamespace())),
    )
    toolset = capability.local
    client = toolset.client
    config = client.transport.config
    servers = config.mcpServers

    assert set(servers) == {"repo", "repo-2"}
    assert servers["repo"].url == "https://repo.example/mcp"
    assert servers["repo"].headers == {"Authorization": "Bearer secret"}
    assert servers["repo-2"].command == "python"
    assert servers["repo-2"].args == ["server.py"]
    assert servers["repo-2"].env == {"TOKEN": "secret"}

    metadata = bridge.get_session_metadata(session, Agent(TestModel()))
    assert metadata == {
        "allowed_tools": None,
        "cache_prompts": True,
        "cache_resources": True,
        "cache_tools": True,
        "include_instructions": True,
        "include_return_schema": True,
        "require_approval": False,
        "server_count": 2,
        "servers": [
            {
                "header_names": ["Authorization"],
                "name": "repo",
                "transport": "http",
                "url": "https://repo.example/mcp",
            },
            {
                "args": ["server.py"],
                "command": "python",
                "env_names": ["TOKEN"],
                "name": "repo",
                "transport": "stdio",
            },
        ],
        "tool_error_behavior": "retry",
        "tool_name_prefixes": ["repo_"],
    }

    session.mcp_servers = []
    empty_capability = await capabilities[0].for_run(cast("Any", SimpleNamespace()))
    assert empty_capability is capabilities[0]

    session.mcp_servers = [
        {
            "name": "updated",
            "transport": "http",
            "url": "https://updated.example/mcp",
        }
    ]
    updated_capability = cast(
        "Any",
        await capabilities[0].for_run(cast("Any", SimpleNamespace())),
    )
    assert set(updated_capability.local.client.transport.config.mcpServers) == {"updated"}

    approval_capability = cast(
        "Any",
        await SessionMcpBridge(require_approval=True)
        .build_agent_capabilities(session)[0]
        .for_run(cast("Any", SimpleNamespace())),
    )
    assert type(approval_capability.get_toolset()).__name__ == "ApprovalRequiredToolset"


@pytest.mark.asyncio
async def test_session_mcp_bridge_rejects_invalid_server_shapes_and_reports_mcp_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AcpSessionContext(
        session_id="session-mcp-invalid",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    bridge = SessionMcpBridge()
    assert bridge.get_session_metadata(session, Agent(TestModel())) is None

    original_import = builtins.__import__

    def fail_mcp_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pydantic_ai.capabilities":
            raise ImportError("MCP extra unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_mcp_import)
    assert fail_mcp_import("json").__name__ == "json"
    populated_session = AcpSessionContext(
        session_id="session-mcp-import-error",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        mcp_servers=[{"name": "repo", "transport": "stdio", "command": "python"}],
    )
    capability = bridge.build_agent_capabilities(populated_session)[0]
    with pytest.raises(ImportError, match="Pydantic AI MCP support"):
        await capability.for_run(cast("Any", SimpleNamespace()))

    assert mcp_module._session_mcp_config([{"name": "invalid", "transport": "stdio"}]) is None
    assert mcp_module._session_mcp_config(
        [
            {
                "name": "headerless",
                "transport": "http",
                "url": "https://repo.example/mcp",
            },
        ],
    ) == {
        "mcpServers": {
            "headerless": {
                "transport": "http",
                "url": "https://repo.example/mcp",
            },
        },
    }
    assert mcp_module._session_mcp_server_config({"transport": "stdio"}, "stdio") is None
    assert mcp_module._session_mcp_server_config({"transport": "http"}, "http") is None
    assert mcp_module._session_mcp_server_config({"transport": "acp"}, "acp") is None
    assert mcp_module._session_mcp_metadata([{"name": "missing-transport"}]) == []
    assert mcp_module._session_mcp_metadata([{"name": "unsupported", "transport": "acp"}]) == [
        {"name": "unsupported", "transport": "acp"},
    ]
    assert mcp_module._unique_session_mcp_name("repo", {"repo": {}, "repo-2": {}}) == "repo-3"
    assert mcp_module._json_string_value("") is None
    assert mcp_module._json_string_sequence("not-a-list") == []
    assert mcp_module._json_string_mapping("not-a-mapping") == {}


def test_mcp_bridge_tool_scope_and_config_only_metadata() -> None:
    session = AcpSessionContext(
        session_id="session-mcp-tool",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    agent = Agent(TestModel(custom_output_text="unused"))
    bridge = McpBridge(
        approval_policy_scope="tool",
        servers=[
            McpServerDefinition(
                server_id="docs",
                name="Docs",
                transport="sse",
            ),
        ],
        config_options=[
            SessionConfigOptionBoolean(
                id="enabled",
                name="Enabled",
                current_value=True,
                type="boolean",
            ),
        ],
    )

    capabilities = bridge.get_mcp_capabilities()
    assert capabilities is not None
    assert capabilities.http is False
    assert capabilities.sse is True
    assert bridge.get_approval_policy_key("missing.tool") is None
    metadata = bridge.get_session_metadata(session, agent)
    assert metadata is not None
    assert "servers" in metadata
    assert metadata["config"] == {"enabled": True}

    config_only_bridge = McpBridge(
        config_options=[
            SessionConfigOptionBoolean(
                id="config_only",
                name="Config Only",
                current_value=False,
                type="boolean",
            ),
        ],
    )
    config_only_metadata = config_only_bridge.get_session_metadata(session, agent)
    assert config_only_metadata == {
        "approval_policy_scope": "tool",
        "config_option_ids": ["config_only"],
        "config": {"config_only": False},
    }
    assert config_only_bridge.get_tool_kind("unknown.tool") is None

    server_scope_bridge = McpBridge(
        approval_policy_scope="server",
        servers=[
            McpServerDefinition(
                server_id="docs",
                name="Docs",
                transport="http",
                tool_prefix="docs.",
            ),
        ],
    )
    assert server_scope_bridge.get_approval_policy_key("docs.search") == "mcp:server:docs"

    prefix_fallback_bridge = McpBridge(
        approval_policy_scope="prefix",
        tools=[McpToolDefinition(tool_name="plain.tool", server_id="missing", kind="execute")],
    )
    assert prefix_fallback_bridge.get_approval_policy_key("plain.tool") == "mcp:tool:plain.tool"
    assert prefix_fallback_bridge._find_config_option("missing") is None
    assert prefix_fallback_bridge._find_server("missing") is None
    assert prefix_fallback_bridge._sync_config_option(
        cast("Any", SimpleNamespace(id="other")),
        session,
    ) == cast("Any", SimpleNamespace(id="other"))

    select_bridge = McpBridge(
        config_options=[
            SessionConfigOptionSelect(
                id="scope",
                name="Scope",
                current_value="repo",
                options=[
                    SessionConfigSelectOption(name="Repo", value="repo"),
                    SessionConfigSelectOption(name="Docs", value="docs"),
                ],
                type="select",
            ),
        ],
        servers=[
            McpServerDefinition(
                server_id="alpha",
                name="Alpha",
                transport="http",
                tool_prefix=None,
            ),
            McpServerDefinition(
                server_id="docs",
                name="Docs",
                transport="http",
                tool_prefix="docs.",
            ),
        ],
    )
    assert select_bridge.get_tool_kind("plain") is None
    assert select_bridge.set_config_option(session, agent, "missing", True) is None
    assert select_bridge.set_config_option(session, agent, "scope", "docs") is not None
    assert select_bridge._find_server("docs") is not None
    assert select_bridge._find_server_for_tool("docs.search") is not None
    synced = select_bridge._sync_config_option(
        select_bridge.config_options[0],
        session,
    )
    assert synced.current_value == "docs"
    custom_bridge = McpBridge(config_options=[cast("Any", SimpleNamespace(id="custom"))])
    custom_options = custom_bridge.set_config_option(session, agent, "custom", "value")
    assert custom_options is not None
    assert session.config_values["custom"] == "value"


def test_mcp_bridge_accepts_grouped_select_options() -> None:
    session = AcpSessionContext(
        session_id="session-mcp-groups",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    agent = Agent(TestModel(custom_output_text="unused"))
    bridge = McpBridge(
        config_options=[
            SessionConfigOptionSelect(
                id="scope",
                name="Scope",
                current_value="repo",
                options=[
                    SessionConfigSelectGroup(
                        group="workspace",
                        name="Workspace",
                        options=[
                            SessionConfigSelectOption(name="Repo", value="repo"),
                            SessionConfigSelectOption(name="Docs", value="docs"),
                        ],
                    ),
                    SessionConfigSelectGroup(
                        group="external",
                        name="External",
                        options=[SessionConfigSelectOption(name="Web", value="web")],
                    ),
                ],
                type="select",
            ),
        ],
    )

    assert bridge.set_config_option(session, agent, "scope", "docs") is not None
    assert bridge.set_config_option(session, agent, "scope", "web") is not None
    assert bridge.set_config_option(session, agent, "scope", "missing") is None


async def test_mcp_bridge_exposes_config_and_routes_server_scoped_approval(
    tmp_path: Path,
) -> None:
    mcp_bridge = McpBridge(
        approval_policy_scope="server",
        config_options=[
            SessionConfigOptionBoolean(
                id="mcp_auto_connect",
                name="Auto Connect",
                category="mcp",
                description="Connect MCP tools automatically.",
                type="boolean",
                current_value=False,
            ),
        ],
        servers=[
            McpServerDefinition(
                server_id="repo",
                name="Repo MCP",
                transport="http",
                tool_prefix="mcp.repo.",
            ),
        ],
        tools=[
            McpToolDefinition(tool_name="mcp.repo.alpha", server_id="repo", kind="read"),
            McpToolDefinition(tool_name="mcp.repo.beta", server_id="repo", kind="read"),
        ],
    )

    def factory(session: AcpSessionContext) -> Agent[None, str]:
        builder = AgentBridgeBuilder(session=session, capability_bridges=[mcp_bridge])
        contributions = builder.build()
        tool_name = "mcp.repo.alpha" if len(session.transcript) <= 1 else "mcp.repo.beta"
        agent = Agent(
            TestModel(call_tools=[tool_name]),
            capabilities=contributions.capabilities,
        )

        @agent.tool(name="mcp.repo.alpha")
        def repo_alpha(ctx: RunContext[None], path: str) -> str:
            if not ctx.tool_call_approved:
                raise ApprovalRequired()
            return f"alpha:{path}"

        @agent.tool(name="mcp.repo.beta")
        def repo_beta(ctx: RunContext[None], path: str) -> str:
            if not ctx.tool_call_approved:  # pragma: no cover
                raise ApprovalRequired()  # pragma: no cover
            return f"beta:{path}"  # pragma: no cover

        return agent

    adapter = create_acp_agent(
        agent_factory=factory,
        config=AdapterConfig(
            approval_bridge=NativeApprovalBridge(enable_persistent_choices=True),
            capability_bridges=[mcp_bridge],
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    client.queue_permission_selected("allow_always")
    adapter.on_connect(client)

    session = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
    assert session.config_options is not None
    auto_connect_option = next(
        option for option in session.config_options if option.id == "mcp_auto_connect"
    )
    assert auto_connect_option.current_value is False

    config_response = await adapter.set_config_option(
        config_id="mcp_auto_connect",
        session_id=session.session_id,
        value=True,
    )

    assert config_response is not None
    updated_auto_connect_option = next(
        option for option in config_response.config_options if option.id == "mcp_auto_connect"
    )
    assert updated_auto_connect_option.current_value is True

    first_prompt = await adapter.prompt(
        prompt=[text_block("Use the first MCP tool.")],
        session_id=session.session_id,
    )
    second_prompt = await adapter.prompt(
        prompt=[text_block("Use the second MCP tool.")],
        session_id=session.session_id,
    )

    assert first_prompt.stop_reason == "end_turn"
    assert second_prompt.stop_reason == "end_turn"
    assert len(client.permission_option_ids) == 1
    assert client.permission_option_ids[0][1] == [
        "allow_once",
        "allow_always",
        "reject_once",
        "reject_always",
    ]
    assert client.permission_option_ids[0][2].kind == "read"

    session_info_updates = [
        update for _, update in client.updates if isinstance(update, SessionInfoUpdate)
    ]
    assert session_info_updates
    session_info = session_info_updates[-1]
    assert session_info.field_meta is not None
    assert session_info.field_meta["pydantic_acp"]["mcp"] == {
        "approval_policy_scope": "server",
        "config": {"mcp_auto_connect": True},
        "config_option_ids": ["mcp_auto_connect"],
        "servers": [
            {
                "description": None,
                "name": "Repo MCP",
                "server_id": "repo",
                "tool_prefix": "mcp.repo.",
                "transport": "http",
                "url": None,
            },
        ],
    }
