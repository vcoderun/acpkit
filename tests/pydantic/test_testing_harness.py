from __future__ import annotations as _annotations

import asyncio

import pytest
from acp import PROTOCOL_VERSION
from pydantic_acp import AdapterConfig, BlackBoxHarness, ClientHostContext, FileSessionStore
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import RunContext

from .support import (
    DemoModelsProvider,
    DemoModesProvider,
    DeniedOutcome,
    Path,
    ToolCallProgress,
    ToolCallStart,
)


def test_black_box_harness_can_drive_approval_write_and_reload(tmp_path: Path) -> None:
    store = FileSessionStore(tmp_path / "sessions")

    def factory(session):
        host = ClientHostContext.from_session(
            client=session.client,
            session=session,
        )
        agent = Agent(TestModel(call_tools=["write_workspace_note"], custom_output_text="done"))

        @agent.tool
        async def write_workspace_note(ctx: RunContext[None], path: str, content: str) -> str:
            del ctx
            await host.filesystem.write_text_file(path, content)
            return "wrote"

        return agent

    harness = BlackBoxHarness.create(
        agent_factory=factory,
        config=AdapterConfig(session_store=store),
    )

    session = asyncio.run(harness.new_session(cwd=str(tmp_path)))
    harness.queue_permission_selected("allow_once")
    prompt_response = asyncio.run(
        harness.prompt_text(
            "Write the workspace note.",
            session_id=session.session_id,
        )
    )

    assert prompt_response.stop_reason == "end_turn"
    tool_starts = harness.updates_of_type(ToolCallStart, session_id=session.session_id)
    tool_progress = harness.updates_of_type(ToolCallProgress, session_id=session.session_id)
    assert any(update.title == "write_workspace_note" for update in tool_starts)
    assert any(update.status == "completed" for update in tool_progress)
    assert harness.client.write_calls == [(session.session_id, "a", "a")]
    assert harness.agent_messages(session_id=session.session_id) == ["done"]

    harness.clear_updates()
    loaded = asyncio.run(
        harness.load_session(
            cwd=str(tmp_path),
            session_id=session.session_id,
        )
    )

    assert loaded is not None
    replayed_messages = harness.agent_messages(session_id=session.session_id)
    assert replayed_messages == ["done"]


def test_black_box_harness_covers_initialize_mode_model_and_default_filters(
    tmp_path: Path,
) -> None:
    harness = BlackBoxHarness.create(
        agent=Agent(TestModel(custom_output_text="base")),
        config=AdapterConfig(
            models_provider=DemoModelsProvider(),
            modes_provider=DemoModesProvider(),
        ),
    )
    missing_session_harness = BlackBoxHarness.create(
        agent=Agent(TestModel(custom_output_text="unused"))
    )

    initialize_response = asyncio.run(harness.initialize())
    with pytest.raises(ValueError, match="No active session id"):
        missing_session_harness.require_session_id()

    harness.queue_permission_cancelled()
    assert isinstance(harness.client.permission_responses[0].outcome, DeniedOutcome)
    assert initialize_response.protocol_version == PROTOCOL_VERSION

    session = asyncio.run(harness.new_session(cwd=str(tmp_path)))
    mode_response = asyncio.run(harness.set_mode("review"))
    model_response = asyncio.run(harness.set_model("provider-model-b"))
    prompt_response = asyncio.run(harness.prompt_text("hello"))

    assert session.session_id == harness.last_session_id
    assert mode_response is not None
    assert model_response is not None
    assert prompt_response.stop_reason == "end_turn"
    assert harness.updates()
    assert harness.tool_updates() == []
    assert harness.agent_messages() == ["provider:model-b"]


def test_black_box_harness_load_session_returns_none_for_missing_state(tmp_path: Path) -> None:
    harness = BlackBoxHarness.create(agent=Agent(TestModel(custom_output_text="missing-session")))
    harness.last_session_id = "missing"

    response = asyncio.run(harness.load_session(cwd=str(tmp_path)))

    assert response is None
    assert harness.last_session_id == "missing"
