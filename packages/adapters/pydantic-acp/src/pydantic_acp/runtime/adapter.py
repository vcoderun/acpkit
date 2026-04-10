from __future__ import annotations as _annotations

import asyncio
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Generic, TypeAlias, TypeVar
from uuid import uuid4

from acp import PROTOCOL_VERSION
from acp.exceptions import RequestError
from acp.interfaces import Client as AcpClient
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    ClientCapabilities,
    CloseSessionResponse,
    ForkSessionResponse,
    HttpMcpServer,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    McpServerStdio,
    NewSessionResponse,
    PlanEntry,
    PromptCapabilities,
    PromptResponse,
    ResumeSessionResponse,
    SessionCapabilities,
    SessionCloseCapabilities,
    SessionForkCapabilities,
    SessionListCapabilities,
    SessionResumeCapabilities,
    SetSessionConfigOptionResponse,
    SetSessionModelResponse,
    SetSessionModeResponse,
    SseMcpServer,
    TextContentBlock,
    ToolCallStart,
)
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai import AgentRunResult
from pydantic_ai import models as pydantic_models
from pydantic_ai.messages import ModelMessage, ToolCallPart
from pydantic_ai.output import OutputSpec
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults

from ..agent_source import AgentSource
from ..approvals import ApprovalResolution
from ..bridges import PrepareToolsBridge
from ..config import AdapterConfig
from ..models import AdapterModel, ModelOverride
from ..providers import ModelSelectionState, ModeState
from ..session.state import (
    AcpSessionContext,
    JsonValue,
    SessionTranscriptUpdate,
    StoredSessionUpdate,
    utc_now,
)
from ._prompt_runtime import NativePlanGeneration, _PromptRuntime
from ._session_runtime import _SessionRuntime
from .bridge_manager import BridgeManager
from .hook_introspection import list_agent_hooks
from .prompts import (
    PromptBlock,
    PromptRunOutcome,
    build_cancelled_history,
    build_error_history,
    build_user_updates,
    derive_title,
    dump_message_history,
    prompt_to_text,
    sanitize_message_history,
    usage_from_run,
)
from .session_surface import (
    ConfigOption,
    SessionSurface,
)
from .slash_commands import parse_slash_command

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)
OutputDataT = TypeVar("OutputDataT", covariant=True)

RunOutputType: TypeAlias = OutputSpec[Any]

__all__ = ("NativePlanGeneration", "PydanticAcpAgent")


class PydanticAcpAgent(Generic[AgentDepsT, OutputDataT]):
    def __init__(
        self,
        agent_source: AgentSource[AgentDepsT, OutputDataT],
        *,
        config: AdapterConfig,
    ) -> None:
        self._agent_source = agent_source
        self._config = config
        self._client: AcpClient | None = None
        self._bridge_manager = BridgeManager(
            base_classifier=config.tool_classifier,
            bridges=tuple(config.capability_bridges),
        )
        self._tool_classifier = self._bridge_manager.tool_classifier
        self._prompt_runtime = _PromptRuntime(self)
        self._session_runtime = _SessionRuntime(self)
        self._active_prompt_tasks: dict[str, asyncio.Task[Any]] = {}

    def on_connect(self, conn: AcpClient) -> None:
        self._client = conn

    def _new_session_id(self) -> str:
        return uuid4().hex

    def _list_agent_hooks(self, agent: PydanticAgent[AgentDepsT, OutputDataT]) -> list[Any]:
        return list_agent_hooks(agent)

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        del client_capabilities, client_info, kwargs
        negotiated_version = min(protocol_version, PROTOCOL_VERSION)
        return InitializeResponse(
            protocol_version=negotiated_version,
            agent_capabilities=AgentCapabilities(
                load_session=True,
                mcp_capabilities=self._bridge_manager.get_mcp_capabilities(),
                prompt_capabilities=PromptCapabilities(),
                session_capabilities=SessionCapabilities(
                    close=SessionCloseCapabilities(),
                    fork=SessionForkCapabilities(),
                    list=SessionListCapabilities(),
                    resume=SessionResumeCapabilities(),
                ),
            ),
            agent_info=Implementation(
                name=self._config.agent_name,
                title=self._config.agent_title,
                version=self._config.agent_version,
            ),
        )

    async def authenticate(self, method_id: str, **kwargs: Any) -> None:
        del method_id, kwargs
        return None

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        del kwargs
        return await self._session_runtime.new_session(cwd, mcp_servers)

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse | None:
        del kwargs
        response = await self._session_runtime.load_session(cwd, session_id, mcp_servers)
        if response is None:
            return None
        return LoadSessionResponse(
            config_options=response.config_options,
            models=response.models,
            modes=response.modes,
        )

    async def list_sessions(
        self,
        cursor: str | None = None,
        cwd: str | None = None,
        **kwargs: Any,
    ) -> ListSessionsResponse:
        del cursor, kwargs
        return await self._session_runtime.list_sessions(cwd=cwd)

    async def prompt(
        self,
        prompt: list[PromptBlock],
        session_id: str,
        message_id: str | None = None,
        **kwargs: Any,
    ) -> PromptResponse:
        del kwargs
        session = self._require_session(session_id)
        current_task = asyncio.current_task()
        if current_task is not None:
            self._active_prompt_tasks[session_id] = current_task
        acknowledged_message_id = message_id or uuid4().hex
        for update in build_user_updates(prompt, message_id=acknowledged_message_id):
            session.transcript.append(StoredSessionUpdate.from_update(update))

        prompt_text = prompt_to_text(prompt)
        slash_command = parse_slash_command(prompt_text)

        if session.title is None and slash_command is None:
            session.title = derive_title(prompt)
        session.updated_at = utc_now()
        self._config.session_store.save(session)

        agent = await self._agent_source.get_agent(session)
        self._configure_agent_runtime(session, agent)
        if slash_command is not None:
            slash_response = await self._handle_slash_command(
                slash_command.name,
                argument=slash_command.argument,
                session=session,
                agent=agent,
            )
            if slash_response is not None:
                response_session = self._require_session(session_id)
                await self._record_update(
                    response_session,
                    AgentMessageChunk(
                        session_update="agent_message_chunk",
                        content=TextContentBlock(type="text", text=slash_response),
                        message_id=uuid4().hex,
                    ),
                )
                response_session.updated_at = utc_now()
                self._config.session_store.save(response_session)
                return PromptResponse(
                    stop_reason="end_turn",
                    usage=None,
                    user_message_id=acknowledged_message_id,
                )
        try:
            try:
                prompt_outcome = await self._run_prompt(agent=agent, prompt=prompt, session=session)
            except asyncio.CancelledError:
                if current_task is not None:
                    current_task.uncancel()
                cancellation_details = "User requested cancellation."
                cancellation_message = "\n".join(
                    (
                        "User stopped the run.",
                        "",
                        "Run details:",
                        cancellation_details,
                    )
                )
                await self._record_update(
                    session,
                    AgentMessageChunk(
                        session_update="agent_message_chunk",
                        content=TextContentBlock(type="text", text=cancellation_message),
                        message_id=uuid4().hex,
                    ),
                )
                session.message_history_json = build_cancelled_history(
                    session.message_history_json,
                    prompt_text=prompt_text,
                    details_text=cancellation_details,
                )
                session.updated_at = utc_now()
                self._config.session_store.save(session)
                return PromptResponse(
                    stop_reason="cancelled",
                    usage=None,
                    user_message_id=acknowledged_message_id,
                )
            except Exception as error:
                session.message_history_json = build_error_history(
                    session.message_history_json,
                    prompt_text=prompt_text,
                    traceback_text="".join(
                        traceback.format_exception(type(error), error, error.__traceback__)
                    ),
                )
                session.updated_at = utc_now()
                self._config.session_store.save(session)
                raise
            result = prompt_outcome.result

            output_text = ""
            if prompt_outcome.stop_reason != "cancelled":
                output_text = self._synchronize_native_plan_output(
                    session,
                    result.output,
                    streamed_output=prompt_outcome.streamed_output,
                )
                if isinstance(result.output, NativePlanGeneration):
                    await self._persist_current_native_plan_state(session, agent=agent)
                if output_text == "" and not prompt_outcome.streamed_output:
                    output_text = self._config.output_serializer.serialize(result.output)
            if output_text:
                await self._record_update(
                    session,
                    AgentMessageChunk(
                        session_update="agent_message_chunk",
                        content=TextContentBlock(type="text", text=output_text),
                        message_id=uuid4().hex,
                    ),
                )

            session.message_history_json = dump_message_history(
                sanitize_message_history(
                    result.all_messages(),
                    error_text=(
                        "Permission request cancelled."
                        if prompt_outcome.stop_reason == "cancelled"
                        else None
                    ),
                )
            )
            session.updated_at = utc_now()
            self._config.session_store.save(session)
            surface = await self._build_session_surface(session, agent)
            await self._emit_session_state_updates(
                session,
                surface,
                emit_available_commands=True,
                emit_config_options=True,
                emit_current_mode=True,
                emit_plan=not self._consume_native_plan_update(session),
                emit_session_info=True,
            )

            return PromptResponse(
                stop_reason=prompt_outcome.stop_reason,
                usage=usage_from_run(result.usage()),
                user_message_id=acknowledged_message_id,
            )
        finally:
            active_task = self._active_prompt_tasks.get(session_id)
            if active_task is current_task:
                self._active_prompt_tasks.pop(session_id, None)

    async def fork_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None = None,
        **kwargs: Any,
    ) -> ForkSessionResponse:
        del kwargs
        response = await self._session_runtime.fork_session(cwd, session_id, mcp_servers)
        return ForkSessionResponse(
            session_id=response.session_id,
            config_options=response.config_options,
            models=response.models,
            modes=response.modes,
        )

    async def resume_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        del kwargs
        return await self._session_runtime.resume_session(cwd, session_id, mcp_servers)

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse | None:
        del kwargs
        if not await self._session_runtime.close_session(session_id):
            return None
        return CloseSessionResponse()

    async def set_session_mode(
        self, mode_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModeResponse | None:
        del kwargs
        return await self._session_runtime.set_session_mode(mode_id, session_id)

    async def set_session_model(
        self, model_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModelResponse | None:
        del kwargs
        return await self._session_runtime.set_session_model(model_id, session_id)

    async def set_config_option(
        self,
        config_id: str,
        session_id: str,
        value: str | bool,
        **kwargs: Any,
    ) -> SetSessionConfigOptionResponse | None:
        del kwargs
        return await self._session_runtime.set_config_option(config_id, session_id, value)

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        del kwargs
        active_task = self._active_prompt_tasks.get(session_id)
        if active_task is not None and not active_task.done():
            active_task.cancel()

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        del params
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        del method, params

    async def _record_update(
        self, session: AcpSessionContext, update: SessionTranscriptUpdate
    ) -> None:
        await self._prompt_runtime._record_update(session, update)

    async def _replay_transcript(self, session: AcpSessionContext) -> None:
        await self._prompt_runtime._replay_transcript(session)

    async def _run_prompt(
        self,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        prompt: list[PromptBlock],
        session: AcpSessionContext,
    ) -> PromptRunOutcome:
        return await self._prompt_runtime._run_prompt(
            agent=agent,
            prompt=prompt,
            session=session,
        )

    async def _record_tool_updates(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        messages: list[ModelMessage],
    ) -> None:
        await self._prompt_runtime._record_tool_updates(session, agent, messages)

    async def _record_bridge_updates(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        await self._prompt_runtime._record_bridge_updates(session, agent)

    async def _run_prompt_with_events(
        self,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        prompt_text: str | None,
        run_kwargs: dict[str, Any],
        session: AcpSessionContext,
    ) -> tuple[AgentRunResult[Any], bool]:
        return await self._prompt_runtime._run_prompt_with_events(
            agent=agent,
            prompt_text=prompt_text,
            run_kwargs=run_kwargs,
            session=session,
        )

    def _known_tool_call_starts(self, session: AcpSessionContext) -> dict[str, ToolCallStart]:
        return self._prompt_runtime._known_tool_call_starts(session)

    def _build_run_kwargs(
        self,
        *,
        message_history: list[ModelMessage] | None,
        deferred_tool_results: DeferredToolResults | None,
        deps: AgentDepsT | None,
        model_override: ModelOverride | None,
        model_settings: ModelSettings | None,
        output_type: RunOutputType | None,
    ) -> dict[str, Any]:
        return self._prompt_runtime._build_run_kwargs(
            message_history=message_history,
            deferred_tool_results=deferred_tool_results,
            deps=deps,
            model_override=model_override,
            model_settings=model_settings,
            output_type=output_type,
        )

    def _normalize_cwd(self, cwd: str) -> Path:
        return self._session_runtime._normalize_cwd(cwd)

    def _configure_agent_runtime(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        self._session_runtime._configure_agent_runtime(session, agent)

    def _set_active_session(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        session: AcpSessionContext,
    ) -> None:
        self._session_runtime._set_active_session(agent, session)

    def _bind_session_client(self, session: AcpSessionContext) -> AcpSessionContext:
        return self._session_runtime._bind_session_client(session)

    async def _build_session_surface(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> SessionSurface:
        return await self._session_runtime._build_session_surface(session, agent)

    async def _emit_session_state_updates(
        self,
        session: AcpSessionContext,
        surface: SessionSurface,
        *,
        emit_available_commands: bool,
        emit_config_options: bool,
        emit_current_mode: bool,
        emit_plan: bool,
        emit_session_info: bool,
    ) -> None:
        await self._session_runtime._emit_session_state_updates(
            session,
            surface,
            emit_available_commands=emit_available_commands,
            emit_config_options=emit_config_options,
            emit_current_mode=emit_current_mode,
            emit_plan=emit_plan,
            emit_session_info=emit_session_info,
        )

    async def _build_config_options(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        model_selection_state: ModelSelectionState | None,
        mode_state: ModeState | None,
    ) -> list[ConfigOption] | None:
        return await self._session_runtime._build_config_options(
            session,
            agent,
            model_selection_state=model_selection_state,
            mode_state=mode_state,
        )

    async def _get_model_selection_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> ModelSelectionState | None:
        return await self._session_runtime._get_model_selection_state(session, agent)

    async def _set_provider_model_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        model_id: str,
    ) -> ModelSelectionState | None:
        return await self._session_runtime._set_provider_model_state(session, agent, model_id)

    def _synchronize_session_model_selection(
        self,
        session: AcpSessionContext,
        model_state: ModelSelectionState | None,
    ) -> None:
        self._session_runtime._synchronize_session_model_selection(session, model_state)

    async def _get_mode_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> ModeState | None:
        return await self._session_runtime._get_mode_state(session, agent)

    async def _set_provider_mode_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        mode_id: str,
    ) -> ModeState | None:
        return await self._session_runtime._set_provider_mode_state(session, agent, mode_id)

    def _synchronize_mode_state(
        self,
        session: AcpSessionContext,
        mode_state: ModeState | None,
    ) -> None:
        self._session_runtime._synchronize_mode_state(session, mode_state)

    async def _get_provider_config_options(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> list[ConfigOption] | None:
        return await self._session_runtime._get_provider_config_options(session, agent)

    async def _set_provider_config_options(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        config_id: str,
        value: str | bool,
    ) -> bool:
        return await self._session_runtime._set_provider_config_options(
            session,
            agent,
            config_id,
            value,
        )

    async def _get_plan_entries(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> list[PlanEntry] | None:
        return await self._session_runtime._get_plan_entries(session, agent)

    async def _synchronize_session_metadata(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        await self._session_runtime._synchronize_session_metadata(session, agent)

    async def _get_approval_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> dict[str, JsonValue] | None:
        return await self._session_runtime._get_approval_state(session, agent)

    def _find_model_option(
        self,
        model_id: str,
        *,
        available_models: Sequence[AdapterModel] | None = None,
    ) -> AdapterModel | None:
        return self._session_runtime._find_model_option(
            model_id,
            available_models=available_models,
        )

    def _require_model_option(self, model_id: str) -> AdapterModel:
        return self._session_runtime._require_model_option(model_id)

    def _resolve_current_model_id(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> str | None:
        return self._session_runtime._resolve_current_model_id(session, agent)

    def _resolve_model_id_from_value(self, model_value: ModelOverride | None) -> str | None:
        return self._session_runtime._resolve_model_id_from_value(model_value)

    async def _resolve_model_override(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> ModelOverride | None:
        return await self._session_runtime._resolve_model_override(session, agent)

    def _supports_fallback_model_selection(self) -> bool:
        return self._session_runtime._supports_fallback_model_selection()

    def _supports_deferred_approval_bridge(self) -> bool:
        return self._prompt_runtime._supports_deferred_approval_bridge()

    async def _resolve_deferred_approvals(
        self,
        *,
        session: AcpSessionContext,
        requests: DeferredToolRequests,
    ) -> ApprovalResolution:
        return await self._prompt_runtime._resolve_deferred_approvals(
            session=session,
            requests=requests,
        )

    async def _record_cancelled_approval(
        self,
        session: AcpSessionContext,
        tool_call: ToolCallPart | None,
    ) -> None:
        await self._prompt_runtime._record_cancelled_approval(session, tool_call)

    def _build_run_output_type(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        session: AcpSessionContext,
    ) -> RunOutputType | None:
        return self._prompt_runtime._build_run_output_type(agent, session=session)

    def _should_stream_text_responses(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        model_override: ModelOverride | None,
        output_type: RunOutputType | None,
    ) -> bool:
        return self._prompt_runtime._should_stream_text_responses(
            agent,
            model_override=model_override,
            output_type=output_type,
        )

    def _contains_text_output(self, output_type: Any) -> bool:
        return self._prompt_runtime._contains_text_output(output_type)

    def _contains_native_plan_generation(self, output_type: Any) -> bool:
        return self._prompt_runtime._contains_native_plan_generation(output_type)

    def _supports_streaming_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        model_override: ModelOverride | None,
    ) -> bool:
        return self._prompt_runtime._supports_streaming_model(
            agent,
            model_override=model_override,
        )

    def _resolve_runtime_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        model_override: ModelOverride | None,
    ) -> pydantic_models.Model:
        return self._prompt_runtime._resolve_runtime_model(
            agent,
            model_override=model_override,
        )

    def _text_chunk_from_event(
        self,
        event: Any,
    ) -> str | None:
        return self._prompt_runtime._text_chunk_from_event(event)

    def _synchronize_native_plan_output(
        self,
        session: AcpSessionContext,
        output: Any,
        *,
        streamed_output: bool,
    ) -> str:
        return self._prompt_runtime._synchronize_native_plan_output(
            session,
            output,
            streamed_output=streamed_output,
        )

    def _native_plan_bridge(
        self,
        session: AcpSessionContext,
    ) -> PrepareToolsBridge[Any] | None:
        return self._prompt_runtime._native_plan_bridge(session)

    def _supports_native_plan_state(self, session: AcpSessionContext) -> bool:
        return self._prompt_runtime._supports_native_plan_state(session)

    def _get_native_plan_entries(self, session: AcpSessionContext) -> list[PlanEntry] | None:
        return self._prompt_runtime._get_native_plan_entries(session)

    def _set_native_plan_state(
        self,
        session: AcpSessionContext,
        *,
        entries: Sequence[PlanEntry],
        plan_markdown: str | None,
    ) -> None:
        self._prompt_runtime._set_native_plan_state(
            session,
            entries=entries,
            plan_markdown=plan_markdown,
        )

    def _format_native_plan(self, session: AcpSessionContext) -> str:
        return self._prompt_runtime._format_native_plan(session)

    async def _persist_current_native_plan_state(
        self,
        session: AcpSessionContext,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        await self._prompt_runtime._persist_current_native_plan_state(
            session,
            agent=agent,
        )

    def _install_native_plan_tools(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        self._prompt_runtime._install_native_plan_tools(agent)

    def _consume_native_plan_update(
        self,
        session: AcpSessionContext,
    ) -> bool:
        return self._prompt_runtime._consume_native_plan_update(session)

    def _model_identity(self, model_value: ModelOverride | None) -> str | None:
        return self._session_runtime._model_identity(model_value)

    async def _handle_slash_command(
        self,
        command_name: str,
        *,
        argument: str | None,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> str | None:
        return await self._session_runtime._handle_slash_command(
            command_name,
            argument=argument,
            session=session,
            agent=agent,
        )

    def _apply_session_model_to_agent(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        session: AcpSessionContext,
    ) -> None:
        self._session_runtime._apply_session_model_to_agent(agent, session)

    def _set_agent_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        session: AcpSessionContext,
        model_id: str,
    ) -> None:
        self._session_runtime._set_agent_model(agent, session, model_id)

    def _restore_default_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        session: AcpSessionContext,
    ) -> bool:
        return self._session_runtime._restore_default_model(agent, session)

    def _restore_agent_default_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> bool:
        return self._session_runtime._restore_agent_default_model(agent)

    def _remember_default_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        self._session_runtime._remember_default_model(agent)

    def _resolve_selected_model(self, model_id: str) -> ModelOverride:
        return self._session_runtime._resolve_selected_model(model_id)

    def _update_session_mcp_servers(
        self,
        session: AcpSessionContext,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None,
    ) -> None:
        self._session_runtime._update_session_mcp_servers(session, mcp_servers)

    def _serialize_mcp_server(
        self,
        server: HttpMcpServer | McpServerStdio | SseMcpServer,
    ) -> dict[str, JsonValue]:
        return self._session_runtime._serialize_mcp_server(server)

    def _require_session(self, session_id: str) -> AcpSessionContext:
        return self._session_runtime._require_session(session_id)
