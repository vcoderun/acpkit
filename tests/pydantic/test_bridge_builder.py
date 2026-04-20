from __future__ import annotations as _annotations

import asyncio

from .support import (
    UTC,
    AcpSessionContext,
    AdapterConfig,
    Agent,
    AgentBridgeBuilder,
    DemoApprovalStateProvider,
    HistoryProcessorBridge,
    HookBridge,
    McpBridge,
    McpServerDefinition,
    McpToolDefinition,
    MemorySessionStore,
    ModelMessage,
    Path,
    PrepareToolsBridge,
    PrepareToolsMode,
    RecordingClient,
    RunContext,
    SessionInfoUpdate,
    TestModel,
    ToolCallProgress,
    ToolCallStart,
    ToolDefinition,
    agent_message_texts,
    create_acp_agent,
    datetime,
    text_block,
)


def test_factory_builder_bridges_enrich_prompt_runtime(tmp_path: Path) -> None:
    hook_bridge = HookBridge()
    history_bridge = HistoryProcessorBridge()

    def chat_tools(
        ctx: RunContext[None],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        del ctx  # pragma: no cover
        return []  # pragma: no cover

    def review_tools(
        ctx: RunContext[None],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        del ctx
        return list(tool_defs)

    prepare_bridge: PrepareToolsBridge[None] = PrepareToolsBridge(
        default_mode_id="chat",
        modes=[
            PrepareToolsMode(
                id="chat",
                name="Chat",
                description="Hide MCP tools in chat mode.",
                prepare_func=chat_tools,
            ),
            PrepareToolsMode(
                id="review",
                name="Review",
                description="Expose MCP tools in review mode.",
                prepare_func=review_tools,
            ),
        ],
    )
    mcp_bridge = McpBridge(
        servers=[
            McpServerDefinition(
                server_id="repo",
                name="Repo MCP",
                transport="http",
                tool_prefix="mcp.",
            )
        ],
        tools=[
            McpToolDefinition(
                tool_name="mcp.search_repo",
                server_id="repo",
                kind="search",
            )
        ],
    )
    bridges = [hook_bridge, history_bridge, prepare_bridge, mcp_bridge]

    def trim_history(messages: list[ModelMessage]) -> list[ModelMessage]:
        return list(messages[-2:])

    def factory(session: AcpSessionContext) -> Agent[None, str]:
        builder = AgentBridgeBuilder(session=session, capability_bridges=bridges)
        contributions = builder.build(plain_history_processors=[trim_history])
        agent = Agent(
            TestModel(call_tools=["mcp.search_repo"], custom_output_text="review:done"),
            capabilities=contributions.capabilities,
            history_processors=contributions.history_processors,
        )

        @agent.tool_plain(name="mcp.search_repo")
        def search_repo(query: str) -> str:
            return f"match:{query}"

        return agent

    adapter = create_acp_agent(
        agent_factory=factory,
        config=AdapterConfig(
            approval_state_provider=DemoApprovalStateProvider(),
            capability_bridges=bridges,
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    initialize_response = asyncio.run(adapter.initialize(protocol_version=1))
    assert initialize_response.agent_capabilities is not None
    assert initialize_response.agent_capabilities.mcp_capabilities is not None
    assert initialize_response.agent_capabilities.mcp_capabilities.http is True

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    assert session.modes is not None
    assert session.modes.current_mode_id == "chat"

    set_mode_response = asyncio.run(
        adapter.set_session_mode(mode_id="review", session_id=session.session_id)
    )

    assert set_mode_response is not None
    client.updates.clear()

    prompt_response = asyncio.run(
        adapter.prompt(
            prompt=[text_block("Search the repo.")],
            session_id=session.session_id,
        )
    )

    assert prompt_response.stop_reason == "end_turn"
    updates = [update for _, update in client.updates]
    tool_starts = [update for update in updates if isinstance(update, ToolCallStart)]
    tool_progress = [update for update in updates if isinstance(update, ToolCallProgress)]
    session_info_updates = [update for update in updates if isinstance(update, SessionInfoUpdate)]
    titles = {update.title for update in tool_starts}
    assert "hook.before_run" in titles
    assert "hook.wrap_run" in titles
    assert "hook.before_node_run" in titles
    assert "hook.wrap_node_run" in titles
    assert "hook.after_node_run" in titles
    assert "hook.before_model_request" in titles
    assert "hook.wrap_model_request" in titles
    assert "history_processor.trim_history" in titles
    assert "hook.prepare_tools" in titles
    assert "prepare_tools.review" in titles
    assert "hook.before_tool_validate" in titles
    assert "hook.wrap_tool_validate" in titles
    assert "hook.after_tool_validate" in titles
    mcp_start = next(update for update in tool_starts if update.title == "mcp.search_repo")
    assert mcp_start.kind == "search"
    assert any(update.title == "hook.after_tool_execute" for update in tool_progress)
    assert any(update.title == "hook.wrap_tool_execute" for update in tool_starts)
    assert agent_message_texts(client) == ["review:done"]

    session_info = session_info_updates[-1]
    assert session_info.field_meta is not None
    metadata = session_info.field_meta["pydantic_acp"]
    assert metadata["approval_state"] == {"policy": "session", "remembered": True}
    assert metadata["hooks"] == {
        "events": [
            "before_run",
            "wrap_run",
            "after_run",
            "on_run_error",
            "before_node_run",
            "wrap_node_run",
            "after_node_run",
            "wrap_run_event_stream",
            "on_event",
            "before_model_request",
            "wrap_model_request",
            "after_model_request",
            "prepare_tools",
            "before_tool_validate",
            "wrap_tool_validate",
            "after_tool_validate",
            "before_tool_execute",
            "wrap_tool_execute",
            "after_tool_execute",
            "on_tool_execute_error",
        ]
    }
    assert metadata["history_processors"] == {"processors": ["trim_history"]}
    assert metadata["mcp"] == {
        "approval_policy_scope": "tool",
        "servers": [
            {
                "description": None,
                "name": "Repo MCP",
                "server_id": "repo",
                "tool_prefix": "mcp.",
                "transport": "http",
                "url": None,
            }
        ],
    }
    assert metadata["prepare_tools"] == {
        "current_mode_id": "review",
        "modes": [
            {
                "description": "Hide MCP tools in chat mode.",
                "id": "chat",
                "name": "Chat",
                "plan_mode": False,
                "plan_tools": False,
            },
            {
                "description": "Expose MCP tools in review mode.",
                "id": "review",
                "name": "Review",
                "plan_mode": False,
                "plan_tools": False,
            },
        ],
    }


def test_history_processor_bridge_wraps_plain_processor() -> None:
    bridge = HistoryProcessorBridge()
    session = AcpSessionContext(
        session_id="session-builder",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    def keep_last_message(messages: list[ModelMessage]) -> list[ModelMessage]:
        return list(messages[-1:])  # pragma: no cover

    wrapped_processor = bridge.wrap_plain_processor(
        session,
        keep_last_message,
        name="keep_last_message",
    )

    assert bridge.processor_names == ["keep_last_message"]
    assert wrapped_processor is not keep_last_message


def test_agent_bridge_builder_auto_wraps_contextual_history_processors(
    tmp_path: Path,
) -> None:
    history_bridge = HistoryProcessorBridge()
    observed_steps: list[int] = []

    def contextual_history(
        ctx: RunContext[None],
        messages: list[ModelMessage],
    ) -> list[ModelMessage]:
        observed_steps.append(ctx.run_step)
        return list(messages)

    def factory(session: AcpSessionContext) -> Agent[None, str]:
        builder = AgentBridgeBuilder(
            session=session,
            capability_bridges=[history_bridge],
        )
        contributions = builder.build(contextual_history_processors=[contextual_history])
        return Agent(
            TestModel(custom_output_text="contextual-history"),
            history_processors=contributions.history_processors,
        )

    adapter = create_acp_agent(
        agent_factory=factory,
        config=AdapterConfig(
            capability_bridges=[history_bridge],
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    prompt_response = asyncio.run(
        adapter.prompt(
            prompt=[text_block("Use contextual history.")],
            session_id=session.session_id,
        )
    )

    assert prompt_response.stop_reason == "end_turn"
    assert observed_steps
    tool_titles = [
        update.title for _, update in client.updates if isinstance(update, ToolCallStart)
    ]
    assert "history_processor.contextual_history" in tool_titles
    assert history_bridge.processor_names == ["contextual_history"]


def test_approval_state_provider_is_exposed_in_session_metadata(tmp_path: Path) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="approval-state")),
        config=AdapterConfig(
            approval_state_provider=DemoApprovalStateProvider(),
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    session_info_updates = [
        update for _, update in client.updates if isinstance(update, SessionInfoUpdate)
    ]
    assert session_info_updates
    field_meta = session_info_updates[-1].field_meta
    assert field_meta is not None
    assert field_meta["pydantic_acp"]["approval_state"] == {
        "policy": "session",
        "remembered": True,
    }
