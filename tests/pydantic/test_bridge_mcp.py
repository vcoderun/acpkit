from __future__ import annotations as _annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

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
            )
        ],
        tools=[
            McpToolDefinition(
                tool_name="mcp.explicit.search",
                server_id="repo",
                kind="search",
            )
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
            )
        ],
        config_options=[
            SessionConfigOptionBoolean(
                id="enabled",
                name="Enabled",
                current_value=True,
                type="boolean",
            )
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
            )
        ]
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
            )
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
        cast(Any, SimpleNamespace(id="other")),
        session,
    ) == cast(Any, SimpleNamespace(id="other"))

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
            )
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
            )
        ]
    )

    assert bridge.set_config_option(session, agent, "scope", "docs") is not None
    assert bridge.set_config_option(session, agent, "scope", "web") is not None
    assert bridge.set_config_option(session, agent, "scope", "missing") is None


def test_mcp_bridge_exposes_config_and_routes_server_scoped_approval(
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
            )
        ],
        servers=[
            McpServerDefinition(
                server_id="repo",
                name="Repo MCP",
                transport="http",
                tool_prefix="mcp.repo.",
            )
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
            if not ctx.tool_call_approved:
                raise ApprovalRequired()
            return f"beta:{path}"

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

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    assert session.config_options is not None
    auto_connect_option = next(
        option for option in session.config_options if option.id == "mcp_auto_connect"
    )
    assert auto_connect_option.current_value is False

    config_response = asyncio.run(
        adapter.set_config_option(
            config_id="mcp_auto_connect",
            session_id=session.session_id,
            value=True,
        )
    )

    assert config_response is not None
    updated_auto_connect_option = next(
        option for option in config_response.config_options if option.id == "mcp_auto_connect"
    )
    assert updated_auto_connect_option.current_value is True

    first_prompt = asyncio.run(
        adapter.prompt(
            prompt=[text_block("Use the first MCP tool.")],
            session_id=session.session_id,
        )
    )
    second_prompt = asyncio.run(
        adapter.prompt(
            prompt=[text_block("Use the second MCP tool.")],
            session_id=session.session_id,
        )
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
            }
        ],
    }
