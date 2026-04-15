from __future__ import annotations as _annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from acp.helpers import text_block
from acp.interfaces import Agent as AcpAgent
from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AllowedOutcome,
    AvailableCommandsUpdate,
    ConfigOptionUpdate,
    ContentToolCallContent,
    CreateTerminalResponse,
    CurrentModeUpdate,
    DeniedOutcome,
    EnvVariable,
    FileEditToolCallContent,
    KillTerminalResponse,
    PermissionOption,
    PlanEntry,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    RequestPermissionResponse,
    SessionConfigOptionBoolean,
    SessionConfigOptionSelect,
    SessionConfigSelectGroup,
    SessionConfigSelectOption,
    SessionInfoUpdate,
    SessionMode,
    TerminalOutputResponse,
    TerminalToolCallContent,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    UsageUpdate,
    UserMessageChunk,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)
from pydantic_acp import (
    AcpSessionContext,
    AdapterConfig,
    AgentBridgeBuilder,
    AgentFactory,
    AgentSource,
    ClientFilesystemBackend,
    ClientHostContext,
    ClientTerminalBackend,
    CompositeProjectionMap,
    ConfigOption,
    FactoryAgentSource,
    FileSessionStore,
    FilesystemBackend,
    FileSystemProjectionMap,
    HistoryProcessorBridge,
    HookBridge,
    HookProjectionMap,
    McpBridge,
    McpServerDefinition,
    McpToolDefinition,
    MemorySessionStore,
    ModelSelectionState,
    ModeState,
    NativeApprovalBridge,
    PrepareToolsBridge,
    PrepareToolsMode,
    StaticAgentSource,
    TerminalBackend,
    ThinkingBridge,
    create_acp_agent,
)
from pydantic_acp.models import AdapterModel
from pydantic_acp.session.state import JsonValue
from pydantic_ai import Agent
from pydantic_ai.exceptions import ApprovalRequired
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import RunContext, ToolDefinition

__all__ = (
    "AcpSessionContext",
    "AdapterConfig",
    "AdapterModel",
    "Agent",
    "AgentBridgeBuilder",
    "AgentFactory",
    "AgentMessageChunk",
    "AgentPlanUpdate",
    "AgentSource",
    "AllowedOutcome",
    "ApprovalRequired",
    "AsyncDemoConfigOptionsProvider",
    "AsyncDemoModesProvider",
    "AsyncDemoPlanProvider",
    "AvailableCommandsUpdate",
    "ClientFilesystemBackend",
    "ClientHostContext",
    "ClientTerminalBackend",
    "CompositeProjectionMap",
    "ContentToolCallContent",
    "ConfigOption",
    "ConfigOptionUpdate",
    "CreateTerminalResponse",
    "CurrentModeUpdate",
    "DemoApprovalStateProvider",
    "DemoConfigOptionsProvider",
    "DemoModelsProvider",
    "DemoModesProvider",
    "DemoPlanProvider",
    "DeniedOutcome",
    "EnvVariable",
    "FactoryAgentSource",
    "FileSessionStore",
    "FileEditToolCallContent",
    "FilesystemBackend",
    "FilesystemRecordingClient",
    "FileSystemProjectionMap",
    "FreeformModelsProvider",
    "HistoryProcessorBridge",
    "HookBridge",
    "HookProjectionMap",
    "HostRecordingClient",
    "JsonValue",
    "KillTerminalResponse",
    "McpBridge",
    "McpServerDefinition",
    "McpToolDefinition",
    "MemorySessionStore",
    "ModeState",
    "ModelMessage",
    "ModelSelectionState",
    "NativeApprovalBridge",
    "Path",
    "PermissionOption",
    "PlanEntry",
    "PrepareToolsBridge",
    "PrepareToolsMode",
    "ReadTextFileResponse",
    "RecordingClient",
    "ReleaseTerminalResponse",
    "RequestPermissionResponse",
    "ReservedModelConfigProvider",
    "RunContext",
    "SessionConfigOptionBoolean",
    "SessionConfigOptionSelect",
    "SessionConfigSelectGroup",
    "SessionConfigSelectOption",
    "SessionInfoUpdate",
    "SessionMode",
    "StaticAgentSource",
    "TerminalToolCallContent",
    "TerminalBackend",
    "TerminalOutputResponse",
    "TerminalRecordingClient",
    "TestModel",
    "ThinkingBridge",
    "ToolCallProgress",
    "ToolCallStart",
    "ToolCallUpdate",
    "ToolDefinition",
    "UTC",
    "UsageUpdate",
    "UserMessageChunk",
    "WaitForTerminalExitResponse",
    "WriteTextFileResponse",
    "agent_message_texts",
    "create_acp_agent",
    "datetime",
    "text_block",
)


class RecordingClient:
    def __init__(self) -> None:
        self.updates: list[tuple[str, Any]] = []
        self.permission_option_ids: list[tuple[str, list[str], ToolCallUpdate]] = []
        self.permission_responses: list[RequestPermissionResponse] = []

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
        self.updates.append((session_id, update))

    async def write_text_file(
        self, content: str, path: str, session_id: str, **kwargs: Any
    ) -> WriteTextFileResponse | None:
        del content, path, session_id, kwargs
        raise AssertionError("filesystem flow is not part of this test")

    async def read_text_file(
        self,
        path: str,
        session_id: str,
        limit: int | None = None,
        line: int | None = None,
        **kwargs: Any,
    ) -> ReadTextFileResponse:
        del path, session_id, limit, line, kwargs
        raise AssertionError("filesystem flow is not part of this test")

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
        raise AssertionError("terminal flow is not part of this test")

    async def terminal_output(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> TerminalOutputResponse:
        del session_id, terminal_id, kwargs
        raise AssertionError("terminal flow is not part of this test")

    async def release_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> ReleaseTerminalResponse | None:
        del session_id, terminal_id, kwargs
        raise AssertionError("terminal flow is not part of this test")

    async def wait_for_terminal_exit(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> WaitForTerminalExitResponse:
        del session_id, terminal_id, kwargs
        raise AssertionError("terminal flow is not part of this test")

    async def kill_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> KillTerminalResponse | None:
        del session_id, terminal_id, kwargs
        raise AssertionError("terminal flow is not part of this test")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError(f"unexpected extension method: {method!r} {params!r}")

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        raise AssertionError(f"unexpected extension notification: {method!r} {params!r}")

    def on_connect(self, conn: AcpAgent) -> None:
        del conn


def agent_message_texts(client: RecordingClient) -> list[str]:
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


class FilesystemRecordingClient(RecordingClient):
    def __init__(self) -> None:
        super().__init__()
        self.read_calls: list[tuple[str, str, int | None, int | None]] = []
        self.write_calls: list[tuple[str, str, str]] = []
        self.write_response: WriteTextFileResponse | None = WriteTextFileResponse()

    async def write_text_file(
        self, content: str, path: str, session_id: str, **kwargs: Any
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


class TerminalRecordingClient(RecordingClient):
    def __init__(self) -> None:
        super().__init__()
        self.create_calls: list[
            tuple[
                str,
                str,
                list[str] | None,
                str | None,
                list[EnvVariable] | None,
                int | None,
            ]
        ] = []
        self.output_calls: list[tuple[str, str]] = []
        self.release_calls: list[tuple[str, str]] = []
        self.wait_calls: list[tuple[str, str]] = []
        self.kill_calls: list[tuple[str, str]] = []
        self.release_response: ReleaseTerminalResponse | None = ReleaseTerminalResponse()
        self.kill_response: KillTerminalResponse | None = KillTerminalResponse()
        self.wait_response = WaitForTerminalExitResponse(exit_code=0)

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
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> TerminalOutputResponse:
        del kwargs
        self.output_calls.append((session_id, terminal_id))
        return TerminalOutputResponse(output="terminal-output", truncated=False)

    async def release_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> ReleaseTerminalResponse | None:
        del kwargs
        self.release_calls.append((session_id, terminal_id))
        return self.release_response

    async def wait_for_terminal_exit(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> WaitForTerminalExitResponse:
        del kwargs
        self.wait_calls.append((session_id, terminal_id))
        return self.wait_response

    async def kill_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> KillTerminalResponse | None:
        del kwargs
        self.kill_calls.append((session_id, terminal_id))
        return self.kill_response


class HostRecordingClient(FilesystemRecordingClient, TerminalRecordingClient):
    pass


class DemoModelsProvider:
    def __init__(self) -> None:
        self._models = [
            AdapterModel(
                model_id="provider-model-a",
                name="Provider Model A",
                override=TestModel(
                    custom_output_text="provider:model-a",
                    model_name="provider-model-a",
                ),
            ),
            AdapterModel(
                model_id="provider-model-b",
                name="Provider Model B",
                override=TestModel(
                    custom_output_text="provider:model-b",
                    model_name="provider-model-b",
                ),
            ),
        ]
        self._model_ids = {model.model_id for model in self._models}

    async def get_model_state(
        self,
        session: AcpSessionContext,
        agent: Agent[Any, Any],
    ) -> ModelSelectionState:
        del agent
        current_model_id = str(session.config_values.get("provider_model", "provider-model-a"))
        return ModelSelectionState(
            available_models=list(self._models),
            current_model_id=current_model_id,
            config_option_name="Provider Model",
            config_option_description="Provider-backed model override.",
        )

    async def set_model(
        self,
        session: AcpSessionContext,
        agent: Agent[Any, Any],
        model_id: str,
    ) -> ModelSelectionState:
        if model_id not in self._model_ids:
            raise AssertionError(f"unexpected model id: {model_id}")
        session.config_values["provider_model"] = model_id
        return await self.get_model_state(session, agent)


class DemoModesProvider:
    def __init__(self) -> None:
        self._modes = [
            SessionMode(
                id="chat",
                name="Chat",
                description="General conversational mode.",
            ),
            SessionMode(
                id="review",
                name="Review",
                description="Focused review mode.",
            ),
        ]
        self._mode_ids = {mode.id for mode in self._modes}

    def get_mode_state(
        self,
        session: AcpSessionContext,
        agent: Agent[Any, Any],
    ) -> ModeState:
        del agent
        current_mode_id = str(session.config_values.get("provider_mode", "chat"))
        return ModeState(
            modes=list(self._modes),
            current_mode_id=current_mode_id,
        )

    def set_mode(
        self,
        session: AcpSessionContext,
        agent: Agent[Any, Any],
        mode_id: str,
    ) -> ModeState:
        if mode_id not in self._mode_ids:
            raise AssertionError(f"unexpected mode id: {mode_id}")
        session.config_values["provider_mode"] = mode_id
        return self.get_mode_state(session, agent)


class DemoConfigOptionsProvider:
    def get_config_options(
        self,
        session: AcpSessionContext,
        agent: Agent[Any, Any],
    ) -> list[ConfigOption]:
        del agent
        stream_enabled = bool(session.config_values.get("stream_enabled", False))
        return [
            SessionConfigOptionBoolean(
                id="stream_enabled",
                name="Streaming",
                category="runtime",
                description="Enable streamed responses.",
                type="boolean",
                current_value=stream_enabled,
            )
        ]

    def set_config_option(
        self,
        session: AcpSessionContext,
        agent: Agent[Any, Any],
        config_id: str,
        value: str | bool,
    ) -> list[ConfigOption] | None:
        if config_id != "stream_enabled" or not isinstance(value, bool):
            return None
        session.config_values["stream_enabled"] = value
        return self.get_config_options(session, agent)


class DemoPlanProvider:
    def get_plan(
        self,
        session: AcpSessionContext,
        agent: Agent[Any, Any],
    ) -> list[PlanEntry]:
        del agent
        current_mode = str(session.config_values.get("mode", "chat"))
        stream_enabled = bool(session.config_values.get("stream_enabled", False))
        return [
            PlanEntry(content=f"mode:{current_mode}", priority="high", status="in_progress"),
            PlanEntry(
                content=f"stream:{str(stream_enabled).lower()}",
                priority="low",
                status="pending",
            ),
        ]


class FreeformModelsProvider:
    async def get_model_state(
        self,
        session: AcpSessionContext,
        agent: Agent[Any, Any],
    ) -> ModelSelectionState:
        del agent
        current_model_id = str(session.config_values.get("freeform_model", "custom-model-a"))
        return ModelSelectionState(
            available_models=[
                AdapterModel(
                    model_id="custom-model-a",
                    name="Custom Model A",
                    override="custom-model-a",
                )
            ],
            current_model_id=current_model_id,
            allow_any_model_id=True,
        )

    async def set_model(
        self,
        session: AcpSessionContext,
        agent: Agent[Any, Any],
        model_id: str,
    ) -> ModelSelectionState:
        session.config_values["freeform_model"] = model_id
        return await self.get_model_state(session, agent)


class ReservedModelConfigProvider:
    def get_config_options(
        self,
        session: AcpSessionContext,
        agent: Agent[Any, Any],
    ) -> list[ConfigOption]:
        del agent
        current_value = str(session.config_values.get("provider_model_option", "provider-model-a"))
        return [
            SessionConfigOptionSelect(
                id="model",
                name="Provider Model Config",
                category="runtime",
                description="Provider-owned model config option.",
                type="select",
                current_value=current_value,
                options=[
                    SessionConfigSelectOption(value="provider-model-a", name="Provider Model A"),
                    SessionConfigSelectOption(value="provider-model-b", name="Provider Model B"),
                ],
            )
        ]

    def set_config_option(
        self,
        session: AcpSessionContext,
        agent: Agent[Any, Any],
        config_id: str,
        value: str | bool,
    ) -> list[ConfigOption] | None:
        if config_id != "model" or not isinstance(value, str):
            return None
        session.config_values["provider_model_option"] = value
        return self.get_config_options(session, agent)


class AsyncDemoModesProvider:
    def __init__(self) -> None:
        self._delegate = DemoModesProvider()

    async def get_mode_state(
        self,
        session: AcpSessionContext,
        agent: Agent[Any, Any],
    ) -> ModeState:
        return self._delegate.get_mode_state(session, agent)

    async def set_mode(
        self,
        session: AcpSessionContext,
        agent: Agent[Any, Any],
        mode_id: str,
    ) -> ModeState:
        return self._delegate.set_mode(session, agent, mode_id)


class AsyncDemoConfigOptionsProvider:
    def __init__(self) -> None:
        self._delegate = DemoConfigOptionsProvider()

    async def get_config_options(
        self,
        session: AcpSessionContext,
        agent: Agent[Any, Any],
    ) -> list[ConfigOption]:
        return self._delegate.get_config_options(session, agent)

    async def set_config_option(
        self,
        session: AcpSessionContext,
        agent: Agent[Any, Any],
        config_id: str,
        value: str | bool,
    ) -> list[ConfigOption] | None:
        return self._delegate.set_config_option(session, agent, config_id, value)


class AsyncDemoPlanProvider:
    def __init__(self) -> None:
        self._delegate = DemoPlanProvider()

    async def get_plan(
        self,
        session: AcpSessionContext,
        agent: Agent[Any, Any],
    ) -> list[PlanEntry]:
        return self._delegate.get_plan(session, agent)


class DemoApprovalStateProvider:
    def get_approval_state(
        self,
        session: AcpSessionContext,
        agent: Agent[Any, Any],
    ) -> dict[str, JsonValue]:
        del agent
        return {
            "policy": "session",
            "remembered": bool(session.config_values.get("remembered_approval", True)),
        }
