from __future__ import annotations as _annotations

import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from acp.interfaces import Agent as AcpAgent
from acp.schema import (
    AgentMessageChunk,
    AllowedOutcome,
    ContentToolCallContent,
    CreateTerminalResponse,
    DeniedOutcome,
    EnvVariable,
    FileEditToolCallContent,
    KillTerminalResponse,
    PermissionOption,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    RequestPermissionResponse,
    TerminalOutputResponse,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from typing_extensions import override

__all__ = (
    "ContentToolCallContent",
    "FileEditToolCallContent",
    "GenericFakeChatModel",
    "RecordingACPClient",
    "ToolCallProgress",
    "ToolCallStart",
    "agent_message_texts",
    "text_block",
)


class GenericFakeChatModel(BaseChatModel):
    messages: Iterator[AIMessage | str]
    stream_delimiter: str | None = None

    @override
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        message = next(self.messages)
        resolved = AIMessage(content=message) if isinstance(message, str) else message
        return ChatResult(generations=[ChatGeneration(message=resolved)])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        chat_result = self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        message = chat_result.generations[0].message
        if not isinstance(message, AIMessage):
            raise ValueError(f"Expected `AIMessage`, got {type(message).__name__}.")

        content = message.content
        tool_calls = message.tool_calls if hasattr(message, "tool_calls") else []
        if content:
            if not isinstance(content, str):
                raise ValueError("Expected string content.")
            if self.stream_delimiter is None:
                content_chunks = [content]
            else:
                content_chunks = cast(list[str], re.split(self.stream_delimiter, content))
                content_chunks = [chunk for chunk in content_chunks if chunk]
            for index, token in enumerate(content_chunks):
                is_last = index == len(content_chunks) - 1
                chunk = ChatGenerationChunk(
                    message=AIMessageChunk(
                        content=token,
                        id=message.id,
                        tool_calls=tool_calls if is_last else [],
                        chunk_position="last" if is_last else None,
                    )
                )
                if run_manager is not None:
                    run_manager.on_llm_new_token(token, chunk=chunk)
                yield chunk
            return

        if tool_calls:
            chunk = ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    id=message.id,
                    tool_calls=tool_calls,
                    chunk_position="last",
                )
            )
            if run_manager is not None:
                run_manager.on_llm_new_token("", chunk=chunk)
            yield chunk

    @property
    def _llm_type(self) -> str:
        return "generic-fake-chat-model"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        del tools, tool_choice, kwargs
        return self


@dataclass(slots=True, kw_only=True)
class RecordingACPClient:
    updates: list[tuple[str, Any]] = field(default_factory=list)
    permission_requests: list[tuple[str, list[str], ToolCallUpdate]] = field(default_factory=list)
    permission_responses: list[RequestPermissionResponse] = field(default_factory=list)
    write_response: WriteTextFileResponse | None = field(default_factory=WriteTextFileResponse)
    release_response: ReleaseTerminalResponse | None = field(
        default_factory=ReleaseTerminalResponse
    )
    kill_response: KillTerminalResponse | None = field(default_factory=KillTerminalResponse)
    wait_response: WaitForTerminalExitResponse = field(
        default_factory=lambda: WaitForTerminalExitResponse(exit_code=0)
    )
    terminal_output_response: TerminalOutputResponse = field(
        default_factory=lambda: TerminalOutputResponse(output="terminal-output", truncated=False)
    )

    def queue_permission_selected(self, option_id: str) -> None:
        self.permission_responses.append(
            RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id=option_id)
            )
        )

    def queue_permission_cancelled(self) -> None:
        self.permission_responses.append(
            RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        )

    async def request_permission(
        self,
        options: list[PermissionOption],
        session_id: str,
        tool_call: ToolCallUpdate,
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        del kwargs
        self.permission_requests.append(
            (session_id, [option.option_id for option in options], tool_call)
        )
        if not self.permission_responses:
            raise AssertionError("unexpected permission request")
        return self.permission_responses.pop(0)

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        del kwargs
        self.updates.append((session_id, update))

    async def write_text_file(
        self,
        content: str,
        path: str,
        session_id: str,
        **kwargs: Any,
    ) -> WriteTextFileResponse | None:
        del content, path, session_id, kwargs
        return self.write_response

    async def read_text_file(
        self,
        path: str,
        session_id: str,
        limit: int | None = None,
        line: int | None = None,
        **kwargs: Any,
    ) -> ReadTextFileResponse:
        del session_id, limit, line, kwargs
        return ReadTextFileResponse(content=f"file:{path}")

    async def create_terminal(
        self,
        command: str,
        session_id: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: list[EnvVariable] | None = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> CreateTerminalResponse:
        del command, session_id, args, cwd, env, output_byte_limit, kwargs
        return CreateTerminalResponse(terminal_id="terminal-1")

    async def terminal_output(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> TerminalOutputResponse:
        del session_id, terminal_id, kwargs
        return self.terminal_output_response

    async def release_terminal(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> ReleaseTerminalResponse | None:
        del session_id, terminal_id, kwargs
        return self.release_response

    async def wait_for_terminal_exit(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> WaitForTerminalExitResponse:
        del session_id, terminal_id, kwargs
        return self.wait_response

    async def kill_terminal(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> KillTerminalResponse | None:
        del session_id, terminal_id, kwargs
        return self.kill_response

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError(f"unexpected extension method: {method!r} {params!r}")

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        raise AssertionError(f"unexpected extension notification: {method!r} {params!r}")

    def on_connect(self, conn: AcpAgent) -> None:
        del conn


def text_block(text: str) -> TextContentBlock:
    return TextContentBlock(type="text", text=text)


def agent_message_texts(client: RecordingACPClient) -> list[str]:
    messages: list[str] = []
    current_message_id: str | None = None
    current_text = ""
    anonymous_message_count = 0

    for _, update in client.updates:
        if not isinstance(update, AgentMessageChunk):
            continue
        message_id = update.message_id
        if message_id is None:
            message_id = f"anonymous:{anonymous_message_count}"
            anonymous_message_count += 1
        if current_message_id != message_id:
            if current_message_id is not None:
                messages.append(current_text)
            current_message_id = message_id
            current_text = ""
        current_text += update.content.text

    if current_message_id is not None:
        messages.append(current_text)
    return messages
