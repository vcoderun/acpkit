from __future__ import annotations as _annotations

import asyncio

from pydantic_ai.capabilities import Hooks

from .support import (
    AdapterConfig,
    Agent,
    HookProjectionMap,
    MemorySessionStore,
    RecordingClient,
    TestModel,
    ToolCallProgress,
    ToolCallStart,
    create_acp_agent,
    text_block,
)


def test_existing_before_model_request_hook_emits_acp_updates(tmp_path) -> None:
    hooks = Hooks[None]()

    @hooks.on.before_model_request
    async def annotate_request(ctx, request_context):
        del ctx
        return request_context

    adapter = create_acp_agent(
        agent=Agent(
            TestModel(custom_output_text="hooked"),
            name="hooked-agent",
            capabilities=[hooks],
        ),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Trigger the hook.")],
            session_id=session.session_id,
        )
    )

    start_updates = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallStart)
        and update.title == "Hook Before Model (annotate_request)"
    ]
    progress_updates = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallProgress)
        and update.title == "Hook Before Model (annotate_request)"
    ]

    assert len(start_updates) == 1
    assert start_updates[0].raw_input == {
        "event": "before_model_request",
        "hook": "annotate_request",
    }
    assert start_updates[0].kind == "fetch"
    assert len(progress_updates) == 1
    assert progress_updates[0].status == "completed"
    assert isinstance(progress_updates[0].raw_output, str)
    assert progress_updates[0].raw_output.startswith("messages=")


def test_multiple_registered_hook_callbacks_each_emit_updates(tmp_path) -> None:
    hooks = Hooks[None]()

    @hooks.on.before_model_request
    async def first_request_hook(ctx, request_context):
        del ctx
        return request_context

    @hooks.on.before_model_request
    async def second_request_hook(ctx, request_context):
        del ctx
        return request_context

    adapter = create_acp_agent(
        agent=Agent(
            TestModel(custom_output_text="hooked"),
            capabilities=[hooks],
        ),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Trigger the registered hooks.")],
            session_id=session.session_id,
        )
    )

    start_titles = [
        update.title for _, update in client.updates if isinstance(update, ToolCallStart)
    ]
    progress_titles = [
        update.title for _, update in client.updates if isinstance(update, ToolCallProgress)
    ]

    assert start_titles.count("Hook Before Model (first_request_hook)") == 1
    assert start_titles.count("Hook Before Model (second_request_hook)") == 1
    assert progress_titles.count("Hook Before Model (first_request_hook)") == 1
    assert progress_titles.count("Hook Before Model (second_request_hook)") == 1


def test_tool_filtered_hook_preserves_tool_metadata(tmp_path) -> None:
    hooks = Hooks[None]()

    @hooks.on.before_tool_execute(tools=["echo"])
    async def audit_echo(ctx, *, call, tool_def, args):
        del ctx, call, tool_def
        return args

    agent = Agent(
        TestModel(call_tools=["echo"], custom_output_text="tool-hooked"),
        capabilities=[hooks],
    )

    @agent.tool_plain
    def echo(text: str) -> str:
        return text.upper()

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Call the echo tool.")],
            session_id=session.session_id,
        )
    )

    start_updates = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallStart)
        and update.title == "Hook Before Tool [echo] (audit_echo)"
    ]
    progress_updates = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallProgress)
        and update.title == "Hook Before Tool [echo] (audit_echo)"
    ]

    assert len(start_updates) == 1
    assert start_updates[0].raw_input == {
        "event": "before_tool_execute",
        "hook": "audit_echo",
        "tools": ["echo"],
        "tool_name": "echo",
    }
    assert start_updates[0].kind == "execute"
    assert len(progress_updates) == 1
    assert progress_updates[0].status == "completed"
    assert progress_updates[0].raw_output == "dict"


def test_custom_hook_projection_map_can_hide_and_relabel_events(tmp_path) -> None:
    hooks = Hooks[None]()

    @hooks.on.before_model_request
    async def annotate_request(ctx, request_context):
        del ctx
        return request_context

    adapter = create_acp_agent(
        agent=Agent(
            TestModel(custom_output_text="hooked"),
            capabilities=[hooks],
        ),
        config=AdapterConfig(session_store=MemorySessionStore()),
        projection_maps=[
            HookProjectionMap(
                event_labels={"before_model_request": "Model Hook"},
                show_hook_name_in_title=False,
                include_tool_filters=False,
            )
        ],
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Trigger the hook.")],
            session_id=session.session_id,
        )
    )

    start_update = next(update for _, update in client.updates if isinstance(update, ToolCallStart))
    progress_update = next(
        update for _, update in client.updates if isinstance(update, ToolCallProgress)
    )
    assert start_update.title == "Hook Model Hook"
    assert progress_update.title == "Hook Model Hook"
    assert start_update.raw_input == {
        "event": "before_model_request",
        "hook": "annotate_request",
    }
