from __future__ import annotations as _annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from acp.schema import AgentMessageChunk, PermissionOption, RequestPermissionResponse
from langchain_core.messages import AIMessage

from .support import GenericFakeChatModel, RecordingACPClient, agent_message_texts, text_block


class _RunManager:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    def on_llm_new_token(self, token: str, *, chunk: Any) -> None:
        del chunk
        self.tokens.append(token)


def test_generic_fake_chat_model_support_stream_paths() -> None:
    manager = _RunManager()
    model = GenericFakeChatModel(
        messages=iter([AIMessage(content="alpha,beta", id="stream-1")]),
        stream_delimiter=",",
    )
    chunks = list(model._stream([], run_manager=cast(Any, manager)))
    assert [chunk.message.content for chunk in chunks] == ["alpha", "beta"]
    assert manager.tokens == ["alpha", "beta"]

    tool_manager = _RunManager()
    tool_model = GenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    id="tool-1",
                    tool_calls=[{"name": "demo", "args": {}, "id": "call-1", "type": "tool_call"}],
                )
            ]
        )
    )
    tool_chunks = list(tool_model._stream([], run_manager=cast(Any, tool_manager)))
    assert len(tool_chunks) == 1
    assert tool_manager.tokens == [""]

    silent_model = GenericFakeChatModel(messages=iter([AIMessage(content="", id="silent-1")]))
    assert list(silent_model._stream([])) == []

    invalid_message_model = GenericFakeChatModel(messages=iter(["ignored"]))
    invalid_message_model._generate = cast(  # type: ignore[method-assign]
        Any,
        lambda *args, **kwargs: SimpleNamespace(
            generations=[SimpleNamespace(message=cast(Any, object()))]
        ),
    )
    with pytest.raises(ValueError, match="Expected `AIMessage`"):
        list(invalid_message_model._stream([]))

    invalid_content_model = GenericFakeChatModel(
        messages=iter([AIMessage(content=cast(Any, ["bad-content"]), id="invalid-1")])
    )
    with pytest.raises(ValueError, match="Expected string content"):
        list(invalid_content_model._stream([]))


@pytest.mark.asyncio
async def test_langchain_recording_acp_client_support_helpers() -> None:
    client = RecordingACPClient()
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
    await client.session_update(
        "session-1",
        AgentMessageChunk(
            session_update="agent_message_chunk",
            message_id="message-2",
            content=text_block("world"),
        ),
    )
    client.updates.append(("session-1", object()))
    assert agent_message_texts(client) == ["hello", "world"]

    assert await client.write_text_file("body", "/tmp/demo", "session-1") == client.write_response
    read_response = await client.read_text_file("/tmp/demo", "session-1")
    assert read_response.content == "file:/tmp/demo"
    created = await client.create_terminal("echo hi", "session-1")
    assert created.terminal_id == "terminal-1"
    assert (
        await client.terminal_output("session-1", "terminal-1") == client.terminal_output_response
    )
    assert await client.release_terminal("session-1", "terminal-1") == client.release_response
    assert await client.wait_for_terminal_exit("session-1", "terminal-1") == client.wait_response
    assert await client.kill_terminal("session-1", "terminal-1") == client.kill_response

    with pytest.raises(AssertionError, match="unexpected extension method"):
        await client.ext_method("demo.echo", {"value": 1})
    with pytest.raises(AssertionError, match="unexpected extension notification"):
        await client.ext_notification("demo.note", {"value": 2})

    assert client.on_connect(cast(Any, object())) is None
