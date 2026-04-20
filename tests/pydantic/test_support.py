from __future__ import annotations as _annotations

from pathlib import Path
from typing import Any, cast

import pytest
from acp.schema import AgentMessageChunk, PermissionOption, RequestPermissionResponse
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from .support import (
    UTC,
    AcpSessionContext,
    AsyncDemoConfigOptionsProvider,
    AsyncDemoModesProvider,
    AsyncDemoPlanProvider,
    DemoConfigOptionsProvider,
    DemoModelsProvider,
    DemoModesProvider,
    FilesystemRecordingClient,
    FreeformModelsProvider,
    HostRecordingClient,
    RecordingClient,
    ReservedModelConfigProvider,
    TerminalRecordingClient,
    agent_message_texts,
    datetime,
    text_block,
)


def _session() -> AcpSessionContext:
    created_at = datetime.now(UTC)
    return AcpSessionContext(
        session_id="session-1",
        cwd=Path("/tmp/demo"),
        created_at=created_at,
        updated_at=created_at,
    )


@pytest.mark.asyncio
async def test_pydantic_support_recording_clients_cover_helpers_and_error_paths() -> None:
    client = RecordingClient()
    option = PermissionOption(option_id="allow_once", name="Allow once", kind="allow_once")
    tool_call = cast(Any, object())

    with pytest.raises(AssertionError, match="unexpected permission request"):
        await client.request_permission([option], "session-1", tool_call)

    client.queue_permission_selected("allow_once")
    selected = await client.request_permission([option], "session-1", tool_call)
    assert isinstance(selected, RequestPermissionResponse)

    client.queue_permission_cancelled()
    cancelled = await client.request_permission([option], "session-1", tool_call)
    assert isinstance(cancelled, RequestPermissionResponse)

    update = AgentMessageChunk(
        session_update="agent_message_chunk",
        message_id=None,
        content=text_block("hello"),
    )
    await client.session_update("session-1", update)
    client.updates.append(("session-1", object()))
    assert agent_message_texts(client) == ["hello"]

    with pytest.raises(AssertionError, match="filesystem flow"):
        await client.write_text_file("body", "/tmp/demo", "session-1")
    with pytest.raises(AssertionError, match="filesystem flow"):
        await client.read_text_file("/tmp/demo", "session-1")
    with pytest.raises(AssertionError, match="terminal flow"):
        await client.create_terminal("echo hi", "session-1")
    with pytest.raises(AssertionError, match="terminal flow"):
        await client.terminal_output("session-1", "terminal-1")
    with pytest.raises(AssertionError, match="terminal flow"):
        await client.release_terminal("session-1", "terminal-1")
    with pytest.raises(AssertionError, match="terminal flow"):
        await client.wait_for_terminal_exit("session-1", "terminal-1")
    with pytest.raises(AssertionError, match="terminal flow"):
        await client.kill_terminal("session-1", "terminal-1")
    with pytest.raises(AssertionError, match="unexpected extension method"):
        await client.ext_method("demo.echo", {"value": 1})
    with pytest.raises(AssertionError, match="unexpected extension notification"):
        await client.ext_notification("demo.note", {"value": 2})
    assert client.on_connect(cast(Any, object())) is None

    fs_client = FilesystemRecordingClient()
    assert (
        await fs_client.write_text_file("body", "/tmp/demo", "session-1")
        == fs_client.write_response
    )
    read_response = await fs_client.read_text_file("/tmp/demo", "session-1", limit=5, line=2)
    assert read_response.content == "file:/tmp/demo:2:5"

    terminal_client = TerminalRecordingClient()
    created = await terminal_client.create_terminal("echo hi", "session-1")
    assert created.terminal_id == "terminal-1"
    assert (
        await terminal_client.terminal_output("session-1", "terminal-1")
    ).output == "terminal-output"
    assert (
        await terminal_client.release_terminal("session-1", "terminal-1")
        == terminal_client.release_response
    )
    assert (
        await terminal_client.wait_for_terminal_exit("session-1", "terminal-1")
        == terminal_client.wait_response
    )
    assert (
        await terminal_client.kill_terminal("session-1", "terminal-1")
        == terminal_client.kill_response
    )

    host_client = HostRecordingClient()
    await host_client.write_text_file("body", "/tmp/demo", "session-1")
    await host_client.create_terminal("echo hi", "session-1")
    assert host_client.write_calls == [("session-1", "/tmp/demo", "body")]
    assert host_client.create_calls[0][1] == "echo hi"


@pytest.mark.asyncio
async def test_pydantic_support_providers_cover_valid_and_invalid_paths() -> None:
    session = _session()
    agent = Agent(TestModel())

    models = DemoModelsProvider()
    model_state = await models.get_model_state(session, agent)
    assert model_state.current_model_id == "provider-model-a"
    with pytest.raises(AssertionError, match="unexpected model id"):
        await models.set_model(session, agent, "missing-model")
    updated_model_state = await models.set_model(session, agent, "provider-model-b")
    assert updated_model_state.current_model_id == "provider-model-b"

    modes = DemoModesProvider()
    mode_state = modes.get_mode_state(session, agent)
    assert mode_state.current_mode_id == "chat"
    with pytest.raises(AssertionError, match="unexpected mode id"):
        modes.set_mode(session, agent, "missing-mode")
    updated_mode_state = modes.set_mode(session, agent, "review")
    assert updated_mode_state.current_mode_id == "review"

    config_options = DemoConfigOptionsProvider()
    assert config_options.set_config_option(session, agent, "wrong", True) is None
    assert config_options.set_config_option(session, agent, "stream_enabled", "yes") is None
    updated_options = config_options.set_config_option(session, agent, "stream_enabled", True)
    assert updated_options is not None

    freeform = FreeformModelsProvider()
    freeform_state = await freeform.set_model(session, agent, "custom-model-b")
    assert freeform_state.current_model_id == "custom-model-b"

    reserved = ReservedModelConfigProvider()
    assert reserved.set_config_option(session, agent, "wrong", "value") is None
    updated_reserved = reserved.set_config_option(session, agent, "model", "provider-model-b")
    assert updated_reserved is not None

    async_modes = AsyncDemoModesProvider()
    assert (await async_modes.get_mode_state(session, agent)).current_mode_id == "review"
    assert (await async_modes.set_mode(session, agent, "chat")).current_mode_id == "chat"

    async_config = AsyncDemoConfigOptionsProvider()
    assert await async_config.get_config_options(session, agent)
    assert await async_config.set_config_option(session, agent, "stream_enabled", False) is not None

    async_plan = AsyncDemoPlanProvider()
    plan = await async_plan.get_plan(session, agent)
    assert [entry.content for entry in plan] == ["mode:chat", "stream:false"]
