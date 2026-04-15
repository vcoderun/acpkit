from __future__ import annotations as _annotations

from dataclasses import dataclass, field
from typing import Any

from acp.interfaces import Agent as AcpAgent
from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AllowedOutcome,
    AvailableCommandsUpdate,
    ConfigOptionUpdate,
    CreateTerminalResponse,
    CurrentModeUpdate,
    DeniedOutcome,
    EnvVariable,
    KillTerminalResponse,
    PermissionOption,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    RequestPermissionResponse,
    SessionInfoUpdate,
    TerminalOutputResponse,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    UsageUpdate,
    UserMessageChunk,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)

__all__ = ("RecordingACPClient", "UpdateRecord", "agent_message_texts")


@dataclass(slots=True, frozen=True, kw_only=True)
class UpdateRecord:
    session_id: str
    update: (
        UserMessageChunk
        | AgentMessageChunk
        | AgentThoughtChunk
        | ToolCallStart
        | ToolCallProgress
        | AgentPlanUpdate
        | AvailableCommandsUpdate
        | CurrentModeUpdate
        | ConfigOptionUpdate
        | SessionInfoUpdate
        | UsageUpdate
    )


@dataclass(slots=True, kw_only=True)
class RecordingACPClient:
    updates: list[UpdateRecord] = field(default_factory=list)
    permission_option_ids: list[tuple[str, list[str], ToolCallUpdate]] = field(default_factory=list)
    permission_responses: list[RequestPermissionResponse] = field(default_factory=list)
    read_calls: list[tuple[str, str, int | None, int | None]] = field(default_factory=list)
    write_calls: list[tuple[str, str, str]] = field(default_factory=list)
    create_calls: list[
        tuple[
            str,
            str,
            list[str] | None,
            str | None,
            list[EnvVariable] | None,
            int | None,
        ]
    ] = field(default_factory=list)
    output_calls: list[tuple[str, str]] = field(default_factory=list)
    release_calls: list[tuple[str, str]] = field(default_factory=list)
    wait_calls: list[tuple[str, str]] = field(default_factory=list)
    kill_calls: list[tuple[str, str]] = field(default_factory=list)
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
                outcome=AllowedOutcome(outcome="selected", option_id=option_id),
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
        self.permission_option_ids.append(
            (session_id, [option.option_id for option in options], tool_call)
        )
        if not self.permission_responses:
            raise AssertionError("unexpected permission request")
        return self.permission_responses.pop(0)

    async def session_update(
        self,
        session_id: str,
        update: (
            UserMessageChunk
            | AgentMessageChunk
            | AgentThoughtChunk
            | ToolCallStart
            | ToolCallProgress
            | AgentPlanUpdate
            | AvailableCommandsUpdate
            | CurrentModeUpdate
            | ConfigOptionUpdate
            | SessionInfoUpdate
            | UsageUpdate
        ),
        **kwargs: Any,
    ) -> None:
        del kwargs
        self.updates.append(UpdateRecord(session_id=session_id, update=update))

    async def write_text_file(
        self,
        content: str,
        path: str,
        session_id: str,
        **kwargs: Any,
    ) -> WriteTextFileResponse | None:
        del kwargs
        self.write_calls.append((session_id, path, content))
        return self.write_response

    async def read_text_file(
        self,
        path: str,
        session_id: str,
        limit: int | None = None,
        line: int | None = None,
        **kwargs: Any,
    ) -> ReadTextFileResponse:
        del kwargs
        self.read_calls.append((session_id, path, limit, line))
        return ReadTextFileResponse(content=f"file:{path}:{line}:{limit}")

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
        del kwargs
        self.create_calls.append((session_id, command, args, cwd, env, output_byte_limit))
        return CreateTerminalResponse(terminal_id="terminal-1")

    async def terminal_output(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> TerminalOutputResponse:
        del kwargs
        self.output_calls.append((session_id, terminal_id))
        return self.terminal_output_response

    async def release_terminal(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> ReleaseTerminalResponse | None:
        del kwargs
        self.release_calls.append((session_id, terminal_id))
        return self.release_response

    async def wait_for_terminal_exit(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> WaitForTerminalExitResponse:
        del kwargs
        self.wait_calls.append((session_id, terminal_id))
        return self.wait_response

    async def kill_terminal(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> KillTerminalResponse | None:
        del kwargs
        self.kill_calls.append((session_id, terminal_id))
        return self.kill_response

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError(f"unexpected extension method: {method!r} {params!r}")

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        raise AssertionError(f"unexpected extension notification: {method!r} {params!r}")

    def on_connect(self, conn: AcpAgent) -> None:
        del conn


def agent_message_texts(client: RecordingACPClient) -> list[str]:
    messages: list[str] = []
    current_message_id: str | None = None
    current_text = ""
    anonymous_message_count = 0

    for record in client.updates:
        update = record.update
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
