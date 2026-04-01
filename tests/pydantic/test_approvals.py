from __future__ import annotations as _annotations

import asyncio

from .support import (
    AdapterConfig,
    Agent,
    AgentMessageChunk,
    ApprovalRequired,
    MemorySessionStore,
    Path,
    RecordingClient,
    RunContext,
    TestModel,
    ToolCallProgress,
    ToolCallStart,
    create_acp_agent,
    text_block,
)


def test_deferred_approval_allow_flow_resumes_run(tmp_path: Path) -> None:
    agent = Agent(TestModel(call_tools=["dangerous"]))

    @agent.tool
    def dangerous(ctx: RunContext[None], path: str) -> str:
        if not ctx.tool_call_approved:
            raise ApprovalRequired()
        return f"approved:{path}"

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    client.queue_permission_selected("allow_once")
    adapter.on_connect(client)

    new_session_response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    prompt_response = asyncio.run(
        adapter.prompt(
            prompt=[text_block("Use the dangerous tool.")],
            session_id=new_session_response.session_id,
        )
    )

    assert prompt_response.stop_reason == "end_turn"
    assert client.permission_option_ids
    option_ids = client.permission_option_ids[0][1]
    assert option_ids == ["allow_once", "reject_once"]
    updates = [update for _, update in client.updates]
    assert isinstance(updates[0], ToolCallStart)
    assert isinstance(updates[1], ToolCallProgress)
    assert updates[1].status == "completed"
    assert isinstance(updates[2], AgentMessageChunk)
    assert updates[2].content.text == '{"dangerous":"approved:a"}'


def test_deferred_approval_deny_flow_returns_denial_output(tmp_path: Path) -> None:
    agent = Agent(TestModel(call_tools=["dangerous"]))

    @agent.tool
    def dangerous(ctx: RunContext[None], path: str) -> str:
        if not ctx.tool_call_approved:
            raise ApprovalRequired()
        return f"approved:{path}"

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    client.queue_permission_selected("reject_once")
    adapter.on_connect(client)

    new_session_response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    prompt_response = asyncio.run(
        adapter.prompt(
            prompt=[text_block("Use the dangerous tool.")],
            session_id=new_session_response.session_id,
        )
    )

    assert prompt_response.stop_reason == "end_turn"
    updates = [update for _, update in client.updates]
    assert isinstance(updates[1], ToolCallProgress)
    assert updates[1].status == "failed"
    assert isinstance(updates[2], AgentMessageChunk)
    assert updates[2].content.text == '{"dangerous":"The tool call was denied."}'


def test_deferred_approval_cancel_flow_stops_turn(tmp_path: Path) -> None:
    agent = Agent(TestModel(call_tools=["dangerous"]))

    @agent.tool
    def dangerous(ctx: RunContext[None], path: str) -> str:
        if not ctx.tool_call_approved:
            raise ApprovalRequired()
        return f"approved:{path}"

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    client.queue_permission_cancelled()
    adapter.on_connect(client)

    new_session_response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    prompt_response = asyncio.run(
        adapter.prompt(
            prompt=[text_block("Use the dangerous tool.")],
            session_id=new_session_response.session_id,
        )
    )

    assert prompt_response.stop_reason == "cancelled"
    updates = [update for _, update in client.updates]
    assert len(updates) == 2
    assert isinstance(updates[0], ToolCallStart)
    assert isinstance(updates[1], ToolCallProgress)
    assert updates[1].status == "failed"
    assert updates[1].raw_output == "Permission request cancelled."


def test_prompt_without_generic_tool_projection_omits_tool_updates(
    tmp_path: Path,
) -> None:
    tool_model = TestModel(call_tools=["read_file"], custom_output_text="projection-disabled")
    agent = Agent(tool_model)

    @agent.tool_plain
    def read_file(path: str) -> str:
        return f"contents:{path}"

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(
            enable_generic_tool_projection=False,
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Do not emit tool updates.")],
            session_id=session.session_id,
        )
    )

    assert not any(
        isinstance(update, ToolCallStart | ToolCallProgress) for _, update in client.updates
    )
    agent_messages = [
        update.content.text for _, update in client.updates if isinstance(update, AgentMessageChunk)
    ]
    assert agent_messages == ["projection-disabled"]
