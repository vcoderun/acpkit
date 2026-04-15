from __future__ import annotations as _annotations

import asyncio
from typing import Any, cast

import pytest
from pydantic_acp import RecordingACPClient, UpdateRecord, agent_message_texts

from .support import (
    AgentMessageChunk,
    AllowedOutcome,
    DeniedOutcome,
    PermissionOption,
    TerminalOutputResponse,
    ToolCallProgress,
    ToolCallStart,
    WaitForTerminalExitResponse,
    text_block,
)


def test_recording_acp_client_records_permission_file_and_terminal_calls() -> None:
    client = RecordingACPClient(
        write_response=None,
        release_response=None,
        kill_response=None,
        wait_response=WaitForTerminalExitResponse(exit_code=7),
        terminal_output_response=TerminalOutputResponse(output="captured", truncated=True),
    )
    permission_options = [
        PermissionOption(option_id="allow_once", name="Allow once", kind="allow_once")
    ]
    tool_call = ToolCallProgress(
        session_update="tool_call_update",
        tool_call_id="tool-1",
        status="in_progress",
    )

    async def exercise_client() -> None:
        client.queue_permission_selected("allow_once")
        selected = await client.request_permission(
            permission_options,
            session_id="session-1",
            tool_call=tool_call,
        )
        client.queue_permission_cancelled()
        cancelled = await client.request_permission(
            permission_options,
            session_id="session-1",
            tool_call=tool_call,
        )

        assert isinstance(selected.outcome, AllowedOutcome)
        assert selected.outcome.option_id == "allow_once"
        assert isinstance(cancelled.outcome, DeniedOutcome)

        with pytest.raises(AssertionError, match="unexpected permission request"):
            await client.request_permission(
                permission_options,
                session_id="session-1",
                tool_call=tool_call,
            )

        await client.session_update(
            "session-1",
            AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="msg-1",
                content=text_block("hello"),
            ),
        )

        assert (await client.write_text_file("hello", "notes.txt", "session-1")) is None
        assert (
            await client.read_text_file("notes.txt", "session-1", limit=10, line=2)
        ).content == "file:notes.txt:2:10"
        assert (
            await client.create_terminal(
                "python",
                "session-1",
                args=["-V"],
                cwd="/tmp",
                output_byte_limit=5,
            )
        ).terminal_id == "terminal-1"
        assert (await client.terminal_output("session-1", "terminal-1")).output == "captured"
        assert (await client.release_terminal("session-1", "terminal-1")) is None
        assert (await client.wait_for_terminal_exit("session-1", "terminal-1")).exit_code == 7
        assert (await client.kill_terminal("session-1", "terminal-1")) is None

        with pytest.raises(AssertionError, match="unexpected extension method"):
            await client.ext_method("ext/test", {})
        with pytest.raises(AssertionError, match="unexpected extension notification"):
            await client.ext_notification("ext/test", {})

    asyncio.run(exercise_client())
    client.on_connect(cast(Any, object()))

    assert client.permission_option_ids == [
        ("session-1", ["allow_once"], tool_call),
        ("session-1", ["allow_once"], tool_call),
        ("session-1", ["allow_once"], tool_call),
    ]
    assert len(client.updates) == 1
    assert client.write_calls == [("session-1", "notes.txt", "hello")]
    assert client.read_calls == [("session-1", "notes.txt", 10, 2)]
    assert client.create_calls == [
        ("session-1", "python", ["-V"], "/tmp", None, 5),
    ]
    assert client.output_calls == [("session-1", "terminal-1")]
    assert client.release_calls == [("session-1", "terminal-1")]
    assert client.wait_calls == [("session-1", "terminal-1")]
    assert client.kill_calls == [("session-1", "terminal-1")]


def test_agent_message_texts_groups_streamed_and_anonymous_messages() -> None:
    client = RecordingACPClient(
        updates=[
            UpdateRecord(
                session_id="session-1",
                update=ToolCallStart(
                    session_update="tool_call",
                    tool_call_id="tool-1",
                    title="tool",
                    kind="execute",
                    status="in_progress",
                ),
            ),
            UpdateRecord(
                session_id="session-1",
                update=AgentMessageChunk(
                    session_update="agent_message_chunk",
                    message_id="msg-1",
                    content=text_block("hel"),
                ),
            ),
            UpdateRecord(
                session_id="session-1",
                update=AgentMessageChunk(
                    session_update="agent_message_chunk",
                    message_id="msg-1",
                    content=text_block("lo"),
                ),
            ),
            UpdateRecord(
                session_id="session-1",
                update=AgentMessageChunk(
                    session_update="agent_message_chunk",
                    message_id=None,
                    content=text_block("anon"),
                ),
            ),
            UpdateRecord(
                session_id="session-1",
                update=AgentMessageChunk(
                    session_update="agent_message_chunk",
                    message_id=None,
                    content=text_block("second"),
                ),
            ),
        ]
    )

    assert agent_message_texts(client) == ["hello", "anon", "second"]
    assert agent_message_texts(RecordingACPClient()) == []
