from __future__ import annotations as _annotations

import asyncio
from typing import Any, cast

import pytest
from pydantic_acp.runtime.prompts import dump_message_history, load_message_history
from pydantic_ai import ModelRequest, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from .support import (
    AdapterConfig,
    Agent,
    ApprovalRequired,
    AvailableCommandsUpdate,
    FileEditToolCallContent,
    FileSystemProjectionMap,
    MemorySessionStore,
    Path,
    RecordingClient,
    RunContext,
    TestModel,
    ToolCallProgress,
    ToolCallStart,
    agent_message_texts,
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
    tool_updates = [
        update for update in updates if isinstance(update, ToolCallStart | ToolCallProgress)
    ]
    assert isinstance(tool_updates[0], ToolCallStart)
    assert isinstance(tool_updates[1], ToolCallProgress)
    assert tool_updates[1].status == "completed"
    assert agent_message_texts(client) == ['{"dangerous":"approved:a"}']


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
    tool_updates = [
        update for update in updates if isinstance(update, ToolCallStart | ToolCallProgress)
    ]
    assert isinstance(tool_updates[1], ToolCallProgress)
    assert tool_updates[1].status == "failed"
    assert agent_message_texts(client) == ['{"dangerous":"The tool call was denied."}']


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
    tool_updates = [
        update
        for _, update in client.updates
        if not isinstance(update, AvailableCommandsUpdate)
        and isinstance(update, ToolCallStart | ToolCallProgress)
    ]
    assert len(tool_updates) == 2
    assert isinstance(tool_updates[0], ToolCallStart)
    assert isinstance(tool_updates[1], ToolCallProgress)
    assert tool_updates[1].status == "failed"
    assert tool_updates[1].raw_output == "Permission request cancelled."

    stored_session = cast(Any, adapter)._config.session_store.get(new_session_response.session_id)
    assert stored_session is not None
    message_history = load_message_history(stored_session.message_history_json)
    assert not any(
        isinstance(part, ToolCallPart)
        for message in message_history
        if isinstance(message, ModelResponse)
        for part in message.parts
    )
    assert any(
        isinstance(part, TextPart) and "Permission request cancelled." in part.content
        for message in message_history
        if isinstance(message, ModelResponse)
        for part in message.parts
    )


def test_prompt_error_sanitizes_unprocessed_tool_calls_and_records_traceback(
    tmp_path: Path,
) -> None:
    def route_failing_tool(
        messages: list[ModelRequest | ModelResponse],
        info: AgentInfo,
    ) -> ModelResponse:
        del info
        if messages and isinstance(messages[-1], ModelRequest):
            for part in messages[-1].parts:
                if isinstance(part, UserPromptPart):
                    return ModelResponse(
                        parts=[
                            ToolCallPart(
                                "dangerous",
                                {"path": "boom.txt"},
                                tool_call_id="dangerous-call",
                            )
                        ]
                    )
        raise AssertionError("expected the failing tool call to be requested")

    agent = Agent(FunctionModel(route_failing_tool, model_name="failing-tool-model"))

    @agent.tool
    def dangerous(ctx: RunContext[None], path: str) -> str:
        del ctx, path
        raise RuntimeError("tool exploded")

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session_response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    stored_session = cast(Any, adapter)._config.session_store.get(session_response.session_id)
    assert stored_session is not None
    stored_session.message_history_json = dump_message_history(
        [
            ModelRequest(parts=[UserPromptPart("previous prompt")]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        "dangling_tool",
                        {"path": "a"},
                        tool_call_id="call-1",
                    )
                ],
                model_name="test",
            ),
        ]
    )
    cast(Any, adapter)._config.session_store.save(stored_session)

    with pytest.raises(RuntimeError, match="tool exploded"):
        asyncio.run(
            adapter.prompt(
                prompt=[text_block("Trigger the failing tool.")],
                session_id=session_response.session_id,
            )
        )

    updated_session = cast(Any, adapter)._config.session_store.get(session_response.session_id)
    assert updated_session is not None
    message_history = load_message_history(updated_session.message_history_json)
    assert not any(
        isinstance(part, ToolCallPart)
        for message in message_history
        if isinstance(message, ModelResponse)
        for part in message.parts
    )
    assert any(
        isinstance(part, UserPromptPart) and part.content == "Trigger the failing tool."
        for message in message_history
        if not isinstance(message, ModelResponse)
        for part in message.parts
    )
    assert any(
        isinstance(part, TextPart) and "RuntimeError: tool exploded" in part.content
        for message in message_history
        if isinstance(message, ModelResponse)
        for part in message.parts
    )


def test_deferred_approval_write_projection_keeps_diff_after_approval(
    tmp_path: Path,
) -> None:
    (tmp_path / "a").write_text("before", encoding="utf-8")
    agent = Agent(TestModel(call_tools=["write_file"]))

    @agent.tool
    def write_file(ctx: RunContext[None], path: str, content: str) -> str:
        if not ctx.tool_call_approved:
            raise ApprovalRequired()
        return f"approved:{path}"

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
        projection_maps=[FileSystemProjectionMap(default_write_tool="write_file")],
    )
    client = RecordingClient()
    client.queue_permission_selected("allow_once")
    adapter.on_connect(client)

    new_session_response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    prompt_response = asyncio.run(
        adapter.prompt(
            prompt=[text_block("Use the write tool.")],
            session_id=new_session_response.session_id,
        )
    )

    assert prompt_response.stop_reason == "end_turn"
    tool_updates = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallStart | ToolCallProgress)
    ]
    assert len(tool_updates) >= 2

    tool_start = tool_updates[0]
    tool_progress = tool_updates[1]
    assert isinstance(tool_start, ToolCallStart)
    assert isinstance(tool_progress, ToolCallProgress)
    assert tool_start.content is not None
    assert tool_progress.content is not None

    start_diff = tool_start.content[0]
    progress_diff = tool_progress.content[0]
    assert isinstance(start_diff, FileEditToolCallContent)
    assert isinstance(progress_diff, FileEditToolCallContent)
    assert start_diff.path == "a"
    assert start_diff.old_text == "before"
    assert start_diff.new_text == "a"
    assert progress_diff.path == "a"
    assert progress_diff.old_text == "before"
    assert progress_diff.new_text == "a"
    assert tool_progress.status == "completed"


def test_deferred_approval_write_projection_preserves_pre_write_diff_after_file_changes(
    tmp_path: Path,
) -> None:
    target_path = tmp_path / "a"
    target_path.write_text("before", encoding="utf-8")
    agent = Agent(TestModel(call_tools=["write_file"]))

    @agent.tool
    def write_file(ctx: RunContext[None], path: str, content: str) -> str:
        if not ctx.tool_call_approved:
            raise ApprovalRequired()
        resolved_path = tmp_path / path
        resolved_path.write_text(content, encoding="utf-8")
        return f"approved:{path}"

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
        projection_maps=[FileSystemProjectionMap(default_write_tool="write_file")],
    )
    client = RecordingClient()
    client.queue_permission_selected("allow_once")
    adapter.on_connect(client)

    new_session_response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    prompt_response = asyncio.run(
        adapter.prompt(
            prompt=[text_block("Use the write tool.")],
            session_id=new_session_response.session_id,
        )
    )

    assert prompt_response.stop_reason == "end_turn"
    assert target_path.read_text(encoding="utf-8") == "a"
    tool_updates = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallStart | ToolCallProgress)
    ]
    assert len(tool_updates) >= 2

    tool_start = tool_updates[0]
    tool_progress = tool_updates[1]
    assert isinstance(tool_start, ToolCallStart)
    assert isinstance(tool_progress, ToolCallProgress)
    assert tool_start.content is not None
    assert tool_progress.content is not None

    start_diff = tool_start.content[0]
    progress_diff = tool_progress.content[0]
    assert isinstance(start_diff, FileEditToolCallContent)
    assert isinstance(progress_diff, FileEditToolCallContent)
    assert start_diff.old_text == "before"
    assert start_diff.new_text == "a"
    assert progress_diff.old_text == "before"
    assert progress_diff.new_text == "a"
    assert tool_progress.status == "completed"


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
    assert agent_message_texts(client) == ["projection-disabled"]
