from __future__ import annotations as _annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, Generic, TypeAlias, TypeVar
from uuid import uuid4

from acp import PROTOCOL_VERSION
from acp.exceptions import RequestError
from acp.interfaces import Client as AcpClient
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    AgentPlanUpdate,
    AvailableCommandsUpdate,
    ClientCapabilities,
    CloseSessionResponse,
    ConfigOptionUpdate,
    CurrentModeUpdate,
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
    SessionInfo,
    SessionInfoUpdate,
    SessionListCapabilities,
    SessionResumeCapabilities,
    SetSessionConfigOptionResponse,
    SetSessionModelResponse,
    SetSessionModeResponse,
    SseMcpServer,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
)
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai import AgentRunResult, AgentRunResultEvent
from pydantic_ai import models as pydantic_models
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UserError
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ToolCallPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.output import OutputSpec
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults

from ..agent_source import AgentSource
from ..approvals import ApprovalResolution
from ..awaitables import resolve_value
from ..config import AdapterConfig
from ..models import AdapterModel, ModelOverride
from ..projection import (
    _is_output_tool,
    build_tool_progress_update,
    build_tool_start_update,
    build_tool_updates,
    compose_projection_maps,
    extract_tool_call_locations,
)
from ..providers import ModelSelectionState, ModeState
from ..session.state import (
    AcpSessionContext,
    JsonValue,
    SessionTranscriptUpdate,
    StoredSessionUpdate,
    utc_now,
)
from .bridge_manager import BridgeManager
from .hook_introspection import list_agent_hooks, observe_agent_hooks
from .prompts import (
    PromptBlock,
    PromptRunOutcome,
    build_user_updates,
    contains_deferred_tool_requests,
    derive_title,
    load_message_history,
    prompt_to_text,
    usage_from_run,
)
from .session_surface import (
    ConfigOption,
    SessionSurface,
    build_mode_config_option,
    build_mode_state_from_selection,
    build_model_config_option,
    build_model_state_from_selection,
    find_model_option,
)
from .slash_commands import (
    build_available_commands,
    extract_session_mcp_servers,
    list_agent_tools,
    parse_slash_command,
    render_hook_listing,
    render_mcp_server_listing,
    render_model_message,
    render_tool_listing,
)

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)
OutputDataT = TypeVar("OutputDataT", covariant=True)

RunOutputType: TypeAlias = OutputSpec[Any]

_MAX_DEFERRED_APPROVAL_ROUNDS: Final = 8

__all__ = ("PydanticAcpAgent",)


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

    def on_connect(self, conn: AcpClient) -> None:
        self._client = conn

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
        session = AcpSessionContext(
            session_id=uuid4().hex,
            cwd=self._normalize_cwd(cwd),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self._update_session_mcp_servers(session, mcp_servers)
        self._config.session_store.save(session)
        agent = await self._agent_source.get_agent(session)
        surface = await self._build_session_surface(session, agent)
        await self._emit_session_state_updates(
            session,
            surface,
            emit_available_commands=True,
            emit_config_options=False,
            emit_current_mode=False,
            emit_plan=True,
            emit_session_info=True,
        )
        return NewSessionResponse(
            session_id=session.session_id,
            config_options=surface.config_options,
            models=surface.model_state,
            modes=surface.mode_state,
        )

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse | None:
        del kwargs
        session = self._config.session_store.get(session_id)
        if session is None:
            return None
        session.cwd = self._normalize_cwd(cwd)
        self._update_session_mcp_servers(session, mcp_servers)
        session.updated_at = utc_now()
        self._config.session_store.save(session)
        await self._replay_transcript(session)
        agent = await self._agent_source.get_agent(session)
        surface = await self._build_session_surface(session, agent)
        await self._emit_session_state_updates(
            session,
            surface,
            emit_available_commands=True,
            emit_config_options=False,
            emit_current_mode=False,
            emit_plan=True,
            emit_session_info=True,
        )
        return LoadSessionResponse(
            config_options=surface.config_options,
            models=surface.model_state,
            modes=surface.mode_state,
        )

    async def list_sessions(
        self,
        cursor: str | None = None,
        cwd: str | None = None,
        **kwargs: Any,
    ) -> ListSessionsResponse:
        del cursor, kwargs
        sessions = self._config.session_store.list_sessions()
        if cwd is not None:
            normalized_cwd = str(self._normalize_cwd(cwd))
            sessions = [session for session in sessions if str(session.cwd) == normalized_cwd]
        return ListSessionsResponse(
            sessions=[
                SessionInfo(
                    cwd=str(session.cwd),
                    session_id=session.session_id,
                    title=session.title,
                    updated_at=session.updated_at.isoformat(),
                )
                for session in sessions
            ]
        )

    async def prompt(
        self,
        prompt: list[PromptBlock],
        session_id: str,
        message_id: str | None = None,
        **kwargs: Any,
    ) -> PromptResponse:
        del kwargs
        session = self._require_session(session_id)
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
        self._apply_session_model_to_agent(agent, session)
        if slash_command is not None:
            slash_response = await self._handle_slash_command(
                slash_command.name,
                argument=slash_command.argument,
                session=session,
                agent=agent,
            )
            if slash_response is not None:
                await self._record_update(
                    session,
                    AgentMessageChunk(
                        session_update="agent_message_chunk",
                        content=TextContentBlock(type="text", text=slash_response),
                        message_id=uuid4().hex,
                    ),
                )
                session.updated_at = utc_now()
                self._config.session_store.save(session)
                return PromptResponse(
                    stop_reason="end_turn",
                    usage=None,
                    user_message_id=acknowledged_message_id,
                )
        prompt_outcome = await self._run_prompt(agent=agent, prompt=prompt, session=session)
        result = prompt_outcome.result

        output_text = ""
        if prompt_outcome.stop_reason != "cancelled" and not prompt_outcome.streamed_output:
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

        session.message_history_json = result.all_messages_json().decode("utf-8")
        session.updated_at = utc_now()
        self._config.session_store.save(session)
        surface = await self._build_session_surface(session, agent)
        await self._emit_session_state_updates(
            session,
            surface,
            emit_available_commands=True,
            emit_config_options=True,
            emit_current_mode=True,
            emit_plan=True,
            emit_session_info=True,
        )

        return PromptResponse(
            stop_reason=prompt_outcome.stop_reason,
            usage=usage_from_run(result.usage()),
            user_message_id=acknowledged_message_id,
        )

    async def fork_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None = None,
        **kwargs: Any,
    ) -> ForkSessionResponse:
        del kwargs
        forked_session = self._config.session_store.fork(
            session_id,
            new_session_id=uuid4().hex,
            cwd=self._normalize_cwd(cwd),
        )
        if forked_session is None:
            raise RequestError.invalid_params({"sessionId": session_id})
        self._update_session_mcp_servers(forked_session, mcp_servers)
        agent = await self._agent_source.get_agent(forked_session)
        surface = await self._build_session_surface(forked_session, agent)
        await self._emit_session_state_updates(
            forked_session,
            surface,
            emit_available_commands=True,
            emit_config_options=False,
            emit_current_mode=False,
            emit_plan=True,
            emit_session_info=True,
        )
        return ForkSessionResponse(
            session_id=forked_session.session_id,
            config_options=surface.config_options,
            models=surface.model_state,
            modes=surface.mode_state,
        )

    async def resume_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        del kwargs
        session = self._require_session(session_id)
        session.cwd = self._normalize_cwd(cwd)
        self._update_session_mcp_servers(session, mcp_servers)
        session.updated_at = utc_now()
        self._config.session_store.save(session)
        await self._replay_transcript(session)
        agent = await self._agent_source.get_agent(session)
        surface = await self._build_session_surface(session, agent)
        await self._emit_session_state_updates(
            session,
            surface,
            emit_available_commands=True,
            emit_config_options=False,
            emit_current_mode=False,
            emit_plan=True,
            emit_session_info=True,
        )
        return ResumeSessionResponse(
            config_options=surface.config_options,
            models=surface.model_state,
            modes=surface.mode_state,
        )

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse | None:
        del kwargs
        session = self._config.session_store.get(session_id)
        if session is None:
            return None
        self._config.session_store.delete(session_id)
        return CloseSessionResponse()

    async def set_session_mode(
        self, mode_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModeResponse | None:
        del kwargs
        session = self._require_session(session_id)
        agent = await self._agent_source.get_agent(session)
        mode_state = await self._set_provider_mode_state(session, agent, mode_id)
        if mode_state is None:
            return None
        session.updated_at = utc_now()
        self._config.session_store.save(session)
        surface = await self._build_session_surface(session, agent)
        await self._emit_session_state_updates(
            session,
            surface,
            emit_available_commands=True,
            emit_config_options=True,
            emit_current_mode=True,
            emit_plan=True,
            emit_session_info=True,
        )
        return SetSessionModeResponse()

    async def set_session_model(
        self, model_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModelResponse | None:
        del kwargs
        session = self._require_session(session_id)
        agent = await self._agent_source.get_agent(session)
        if self._config.models_provider is not None:
            model_state = await self._set_provider_model_state(session, agent, model_id)
            if model_state is None:
                return None
        else:
            if not self._supports_fallback_model_selection():
                return None
            model_option = self._require_model_option(model_id)
            self._remember_default_model(agent)
            agent.model = model_option.override
            agent._acp_selected_model_id = model_id  # type: ignore
            session.session_model_id = model_id
            session.config_values["model"] = model_id
        session.updated_at = utc_now()
        self._config.session_store.save(session)
        surface = await self._build_session_surface(session, agent)
        await self._emit_session_state_updates(
            session,
            surface,
            emit_available_commands=True,
            emit_config_options=True,
            emit_current_mode=False,
            emit_plan=True,
            emit_session_info=True,
        )
        return SetSessionModelResponse()

    async def set_config_option(
        self,
        config_id: str,
        session_id: str,
        value: str | bool,
        **kwargs: Any,
    ) -> SetSessionConfigOptionResponse | None:
        del kwargs
        session = self._require_session(session_id)
        agent = await self._agent_source.get_agent(session)
        handled = False
        if config_id == "model":
            if not isinstance(value, str):
                raise RequestError.invalid_params({"configId": config_id, "value": value})
            if await self.set_session_model(value, session_id) is not None:
                handled = True
        elif config_id == "mode":
            if not isinstance(value, str):
                raise RequestError.invalid_params({"configId": config_id, "value": value})
            if await self.set_session_mode(value, session_id) is not None:
                handled = True
        if (
            not handled
            and self._config.config_options_provider is not None
            and await self._set_provider_config_options(session, agent, config_id, value)
        ):
            session.updated_at = utc_now()
            self._config.session_store.save(session)
            handled = True
        if not handled:
            bridge_options = self._bridge_manager.set_config_option(
                session,
                agent,
                config_id,
                value,
            )
            if bridge_options is not None:
                session.updated_at = utc_now()
                self._config.session_store.save(session)
                handled = True
        if not handled:
            return None
        updated_session = self._require_session(session_id)
        updated_agent = await self._agent_source.get_agent(updated_session)
        surface = await self._build_session_surface(updated_session, updated_agent)
        if config_id not in {"model", "mode"}:
            await self._emit_session_state_updates(
                updated_session,
                surface,
                emit_available_commands=True,
                emit_config_options=True,
                emit_current_mode=True,
                emit_plan=True,
                emit_session_info=True,
            )
        return SetSessionConfigOptionResponse(config_options=surface.config_options or [])

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        del session_id, kwargs

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        del params
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        del method, params

    async def _record_update(
        self, session: AcpSessionContext, update: SessionTranscriptUpdate
    ) -> None:
        session.transcript.append(StoredSessionUpdate.from_update(update))
        if self._client is not None:
            await self._client.session_update(session_id=session.session_id, update=update)

    async def _replay_transcript(self, session: AcpSessionContext) -> None:
        if not self._config.replay_history_on_load or self._client is None:
            return
        for stored_update in session.transcript:
            await self._client.session_update(
                session_id=session.session_id, update=stored_update.to_update()
            )

    async def _run_prompt(
        self,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        prompt: list[PromptBlock],
        session: AcpSessionContext,
    ) -> PromptRunOutcome:
        message_history = load_message_history(session.message_history_json)
        deferred_tool_results: DeferredToolResults | None = None
        prompt_text: str | None = prompt_to_text(prompt)

        for _ in range(_MAX_DEFERRED_APPROVAL_ROUNDS):
            deps = await self._agent_source.get_deps(session, agent)
            model_override = await self._resolve_model_override(session, agent)
            run_output_type = self._build_run_output_type(agent)
            run_kwargs = self._build_run_kwargs(
                message_history=message_history,
                deferred_tool_results=deferred_tool_results,
                deps=deps,
                model_override=model_override,
                output_type=run_output_type,
            )
            try:
                use_stream_events = self._should_stream_text_responses(
                    agent,
                    model_override=model_override,
                    output_type=run_output_type,
                )
                if self._config.hook_projection_map is None:
                    if use_stream_events:
                        result, streamed_output = await self._run_prompt_with_events(
                            agent=agent,
                            prompt_text=prompt_text,
                            run_kwargs=run_kwargs,
                            session=session,
                        )
                    else:
                        result = await agent.run(prompt_text, **run_kwargs)
                        streamed_output = False
                else:
                    with observe_agent_hooks(
                        agent,
                        write_update=lambda update: self._record_update(session, update),
                        projection_map=self._config.hook_projection_map,
                    ):
                        if use_stream_events:
                            result, streamed_output = await self._run_prompt_with_events(
                                agent=agent,
                                prompt_text=prompt_text,
                                run_kwargs=run_kwargs,
                                session=session,
                            )
                        else:
                            result = await agent.run(prompt_text, **run_kwargs)
                            streamed_output = False
            except (ModelAPIError, ModelHTTPError, UserError):
                if not self._restore_default_model(agent, session):
                    raise
                self._config.session_store.save(session)
                deps = await self._agent_source.get_deps(session, agent)
                model_override = await self._resolve_model_override(session, agent)
                run_kwargs = self._build_run_kwargs(
                    message_history=message_history,
                    deferred_tool_results=deferred_tool_results,
                    deps=deps,
                    model_override=model_override,
                    output_type=run_output_type,
                )
                use_stream_events = self._should_stream_text_responses(
                    agent,
                    model_override=model_override,
                    output_type=run_output_type,
                )
                if self._config.hook_projection_map is None:
                    if use_stream_events:
                        result, streamed_output = await self._run_prompt_with_events(
                            agent=agent,
                            prompt_text=prompt_text,
                            run_kwargs=run_kwargs,
                            session=session,
                        )
                    else:
                        result = await agent.run(prompt_text, **run_kwargs)
                        streamed_output = False
                else:
                    with observe_agent_hooks(
                        agent,
                        write_update=lambda update: self._record_update(session, update),
                        projection_map=self._config.hook_projection_map,
                    ):
                        if use_stream_events:
                            result, streamed_output = await self._run_prompt_with_events(
                                agent=agent,
                                prompt_text=prompt_text,
                                run_kwargs=run_kwargs,
                                session=session,
                            )
                        else:
                            result = await agent.run(prompt_text, **run_kwargs)
                            streamed_output = False
            if use_stream_events:
                await self._record_bridge_updates(session, agent)
            else:
                await self._record_tool_updates(session, agent, result.new_messages())

            if not isinstance(result.output, DeferredToolRequests):
                return PromptRunOutcome(
                    result=result,
                    stop_reason="end_turn",
                    streamed_output=streamed_output,
                )
            if not self._supports_deferred_approval_bridge():
                return PromptRunOutcome(
                    result=result,
                    stop_reason="end_turn",
                    streamed_output=streamed_output,
                )
            if result.output.calls or not result.output.approvals:
                return PromptRunOutcome(
                    result=result,
                    stop_reason="end_turn",
                    streamed_output=streamed_output,
                )

            session.message_history_json = result.all_messages_json().decode("utf-8")
            session.updated_at = utc_now()
            self._config.session_store.save(session)

            approval_resolution = await self._resolve_deferred_approvals(
                session=session,
                requests=result.output,
            )
            if approval_resolution.cancelled:
                await self._record_cancelled_approval(
                    session,
                    approval_resolution.cancelled_tool_call,
                )
                return PromptRunOutcome(
                    result=result,
                    stop_reason="cancelled",
                    streamed_output=streamed_output,
                )

            message_history = result.all_messages()
            deferred_tool_results = approval_resolution.deferred_tool_results
            prompt_text = None

        raise RequestError.internal_error({"reason": "deferred_approval_loop_exceeded"})

    async def _record_tool_updates(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        messages: list[ModelMessage],
    ) -> None:
        if not self._config.enable_generic_tool_projection:
            return
        for update in build_tool_updates(
            messages,
            classifier=self._tool_classifier,
            cwd=session.cwd,
            known_starts=self._known_tool_call_starts(session),
            projection_map=compose_projection_maps(self._config.projection_maps),
            serializer=self._config.output_serializer,
        ):
            await self._record_update(session, update)
        await self._record_bridge_updates(session, agent)

    async def _record_bridge_updates(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        for update in self._bridge_manager.drain_updates(session, agent):
            await self._record_update(session, update)

    async def _run_prompt_with_events(
        self,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        prompt_text: str | None,
        run_kwargs: dict[str, Any],
        session: AcpSessionContext,
    ) -> tuple[AgentRunResult[Any], bool]:
        known_starts = self._known_tool_call_starts(session)
        message_id = uuid4().hex
        projection_map = compose_projection_maps(self._config.projection_maps)
        streamed_output = False

        async for event in agent.run_stream_events(prompt_text, **run_kwargs):
            if isinstance(event, AgentRunResultEvent):
                return event.result, streamed_output
            if self._config.enable_generic_tool_projection and isinstance(
                event, FunctionToolCallEvent
            ):
                if _is_output_tool(event.part.tool_name) or event.part.tool_call_id in known_starts:
                    continue
                start_update = build_tool_start_update(
                    event.part,
                    classifier=self._tool_classifier,
                    cwd=session.cwd,
                    projection_map=projection_map,
                )
                known_starts[event.part.tool_call_id] = start_update
                await self._record_update(session, start_update)
                continue
            if self._config.enable_generic_tool_projection and isinstance(
                event, FunctionToolResultEvent
            ):
                result_part = event.result
                if isinstance(result_part, RetryPromptPart):
                    if result_part.tool_name is None or _is_output_tool(result_part.tool_name):
                        continue
                elif _is_output_tool(result_part.tool_name):
                    continue
                await self._record_update(
                    session,
                    build_tool_progress_update(
                        result_part,
                        classifier=self._tool_classifier,
                        cwd=session.cwd,
                        known_start=known_starts.get(result_part.tool_call_id),
                        projection_map=projection_map,
                        serializer=self._config.output_serializer,
                    ),
                )
                continue
            text_chunk = self._text_chunk_from_event(event)
            if text_chunk is None or text_chunk == "":
                continue
            streamed_output = True
            await self._record_update(
                session,
                AgentMessageChunk(
                    session_update="agent_message_chunk",
                    content=TextContentBlock(type="text", text=text_chunk),
                    message_id=message_id,
                ),
            )

        raise RequestError.internal_error({"reason": "missing_agent_run_result"})

    def _normalize_cwd(self, cwd: str) -> Path:
        path = Path(cwd).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return path

    def _known_tool_call_starts(self, session: AcpSessionContext) -> dict[str, ToolCallStart]:
        known_starts: dict[str, ToolCallStart] = {}
        for stored_update in session.transcript:
            update = stored_update.to_update()
            if not isinstance(update, ToolCallStart):
                continue
            known_starts[update.tool_call_id] = update
        return known_starts

    async def _build_session_surface(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> SessionSurface:
        model_selection_state = await self._get_model_selection_state(session, agent)
        mode_state = await self._get_mode_state(session, agent)
        config_options = await self._build_config_options(
            session,
            agent,
            model_selection_state=model_selection_state,
            mode_state=mode_state,
        )
        surface = SessionSurface(
            config_options=config_options,
            model_state=build_model_state_from_selection(model_selection_state),
            mode_state=build_mode_state_from_selection(mode_state),
            plan_entries=await self._get_plan_entries(session, agent),
        )
        await self._synchronize_session_metadata(session, agent)
        self._config.session_store.save(session)
        return surface

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
        if self._client is None:
            return
        if emit_session_info and session.metadata:
            await self._client.session_update(
                session_id=session.session_id,
                update=SessionInfoUpdate(
                    session_update="session_info_update",
                    title=session.title,
                    updated_at=session.updated_at.isoformat(),
                    field_meta=session.metadata or None,
                ),
            )
        if emit_available_commands:
            await self._client.session_update(
                session_id=session.session_id,
                update=AvailableCommandsUpdate(
                    session_update="available_commands_update",
                    available_commands=build_available_commands(),
                ),
            )
        if emit_current_mode and surface.mode_state is not None:
            await self._client.session_update(
                session_id=session.session_id,
                update=CurrentModeUpdate(
                    session_update="current_mode_update",
                    current_mode_id=surface.mode_state.current_mode_id,
                ),
            )
        if emit_config_options and surface.config_options is not None:
            await self._client.session_update(
                session_id=session.session_id,
                update=ConfigOptionUpdate(
                    session_update="config_option_update",
                    config_options=surface.config_options,
                ),
            )
        if emit_plan and surface.plan_entries is not None:
            await self._client.session_update(
                session_id=session.session_id,
                update=AgentPlanUpdate(session_update="plan", entries=surface.plan_entries),
            )

    async def _build_config_options(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        model_selection_state: ModelSelectionState | None,
        mode_state: ModeState | None,
    ) -> list[ConfigOption] | None:
        options: list[ConfigOption] = []
        if (
            model_selection_state is not None
            and not model_selection_state.allow_any_model_id
            and model_selection_state.enable_config_option
            and model_selection_state.current_model_id is not None
        ):
            options.append(build_model_config_option(model_selection_state))
        if mode_state is not None and mode_state.current_mode_id is not None and mode_state.modes:
            options.append(build_mode_config_option(mode_state))
        provider_options = await self._get_provider_config_options(session, agent)
        if provider_options is not None:
            reserved_ids = {option.id for option in options}
            options.extend(option for option in provider_options if option.id not in reserved_ids)
        bridge_options = self._bridge_manager.get_config_options(session, agent)
        if bridge_options is not None:
            reserved_ids = {option.id for option in options}
            options.extend(option for option in bridge_options if option.id not in reserved_ids)
        return options or None

    async def _get_model_selection_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> ModelSelectionState | None:
        provider = self._config.models_provider
        if provider is not None:
            model_state = await resolve_value(provider.get_model_state(session, agent))
            self._synchronize_session_model_selection(session, model_state)
            return model_state
        current_model_id = self._resolve_current_model_id(session, agent)
        if current_model_id is None:
            return None
        if not self._supports_fallback_model_selection():
            current_model_value = agent.model
            if not isinstance(current_model_value, pydantic_models.Model | str):
                return None
            return ModelSelectionState(
                available_models=[
                    AdapterModel(
                        model_id=current_model_id,
                        name=current_model_id,
                        override=current_model_value,
                    )
                ],
                current_model_id=current_model_id,
                allow_any_model_id=True,
                enable_config_option=False,
            )
        return ModelSelectionState(
            available_models=list(self._config.available_models),
            current_model_id=current_model_id,
            enable_config_option=self._config.enable_model_config_option,
        )

    async def _set_provider_model_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        model_id: str,
    ) -> ModelSelectionState | None:
        provider = self._config.models_provider
        if provider is None:
            return None
        model_state = await resolve_value(provider.set_model(session, agent, model_id))
        if model_state is None:
            model_state = await resolve_value(provider.get_model_state(session, agent))
        self._synchronize_session_model_selection(session, model_state)
        return model_state

    def _synchronize_session_model_selection(
        self,
        session: AcpSessionContext,
        model_state: ModelSelectionState | None,
    ) -> None:
        if model_state is None:
            return
        session.session_model_id = model_state.current_model_id
        if model_state.current_model_id is None:
            session.config_values.pop("model", None)
            return
        session.config_values["model"] = model_state.current_model_id

    async def _get_mode_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> ModeState | None:
        provider = self._config.modes_provider
        mode_state: ModeState | None = None
        if provider is not None:
            mode_state = await resolve_value(provider.get_mode_state(session, agent))
        if mode_state is None:
            mode_state = self._bridge_manager.get_mode_state(session, agent)
        self._synchronize_mode_state(session, mode_state)
        return mode_state

    async def _set_provider_mode_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        mode_id: str,
    ) -> ModeState | None:
        provider = self._config.modes_provider
        mode_state: ModeState | None = None
        if provider is not None:
            mode_state = await resolve_value(provider.set_mode(session, agent, mode_id))
            if mode_state is None:
                mode_state = await resolve_value(provider.get_mode_state(session, agent))
        if mode_state is None:
            mode_state = self._bridge_manager.set_mode(session, agent, mode_id)
        self._synchronize_mode_state(session, mode_state)
        return mode_state

    def _synchronize_mode_state(
        self,
        session: AcpSessionContext,
        mode_state: ModeState | None,
    ) -> None:
        if mode_state is None or mode_state.current_mode_id is None:
            session.config_values.pop("mode", None)
            return
        session.config_values["mode"] = mode_state.current_mode_id

    async def _get_provider_config_options(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> list[ConfigOption] | None:
        provider = self._config.config_options_provider
        if provider is None:
            return None
        return await resolve_value(provider.get_config_options(session, agent))

    async def _set_provider_config_options(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        config_id: str,
        value: str | bool,
    ) -> bool:
        provider = self._config.config_options_provider
        if provider is None:
            return False
        options = await resolve_value(provider.set_config_option(session, agent, config_id, value))
        if options is not None:
            return True
        current_options = await resolve_value(provider.get_config_options(session, agent))
        if current_options is None:
            return False
        return any(option.id == config_id for option in current_options)

    async def _get_plan_entries(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> list[PlanEntry] | None:
        provider = self._config.plan_provider
        if provider is None:
            return None
        plan_entries = await resolve_value(provider.get_plan(session, agent))
        return list(plan_entries) if plan_entries is not None else None

    async def _synchronize_session_metadata(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        metadata_sections = self._bridge_manager.get_metadata_sections(session, agent)
        approval_state = await self._get_approval_state(session, agent)
        if approval_state is not None:
            metadata_sections["approval_state"] = approval_state
        session.metadata = {"pydantic_acp": metadata_sections} if metadata_sections else {}

    async def _get_approval_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> dict[str, JsonValue] | None:
        provider = self._config.approval_state_provider
        if provider is None:
            return None
        return await resolve_value(provider.get_approval_state(session, agent))

    def _find_model_option(
        self,
        model_id: str,
        *,
        available_models: Sequence[AdapterModel] | None = None,
    ) -> AdapterModel | None:
        return find_model_option(
            model_id,
            available_models=available_models or self._config.available_models,
        )

    def _require_model_option(self, model_id: str) -> AdapterModel:
        model_option = self._find_model_option(model_id)
        if model_option is None:
            raise RequestError.invalid_params({"modelId": model_id})
        return model_option

    def _resolve_current_model_id(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> str | None:
        if session.session_model_id is not None:
            return session.session_model_id
        selected_model_id = getattr(agent, "_acp_selected_model_id", None)
        if isinstance(selected_model_id, str) and selected_model_id:
            return selected_model_id
        model_value = getattr(agent, "model", None)
        return self._resolve_model_id_from_value(model_value)

    def _resolve_model_id_from_value(self, model_value: object) -> str | None:
        model_identity = self._model_identity(model_value)
        for model_option in self._config.available_models:
            if model_option.override is model_value:
                return model_option.model_id
            if model_identity is not None and model_identity == self._model_identity(
                model_option.override
            ):
                return model_option.model_id
        if model_identity is not None:
            return model_identity
        if isinstance(model_value, str) and model_value:
            return model_value
        return None

    async def _resolve_model_override(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> ModelOverride | None:
        if (
            session.session_model_id is not None
            and getattr(agent, "_acp_selected_model_id", None) == session.session_model_id
        ):
            return None
        model_selection_state = await self._get_model_selection_state(session, agent)
        current_model_id = session.session_model_id
        model_options = self._config.available_models
        if model_selection_state is not None:
            current_model_id = model_selection_state.current_model_id
            model_options = model_selection_state.available_models
        if current_model_id is None:
            return None
        model_option = self._find_model_option(
            current_model_id,
            available_models=model_options,
        )
        return model_option.override if model_option is not None else current_model_id

    def _supports_fallback_model_selection(self) -> bool:
        return self._config.allow_model_selection and bool(self._config.available_models)

    def _supports_deferred_approval_bridge(self) -> bool:
        return self._config.approval_bridge is not None

    async def _resolve_deferred_approvals(
        self,
        *,
        session: AcpSessionContext,
        requests: DeferredToolRequests,
    ) -> ApprovalResolution:
        approval_bridge = self._config.approval_bridge
        if approval_bridge is None or self._client is None:
            raise RequestError.internal_error({"reason": "deferred_approval_requires_client"})
        return await approval_bridge.resolve_deferred_approvals(
            client=self._client,
            session=session,
            requests=requests,
            classifier=self._tool_classifier,
        )

    async def _record_cancelled_approval(
        self,
        session: AcpSessionContext,
        tool_call: ToolCallPart | None,
    ) -> None:
        if tool_call is None:
            return
        raw_input = tool_call.args_as_dict()
        await self._record_update(
            session,
            ToolCallProgress(
                session_update="tool_call_update",
                tool_call_id=tool_call.tool_call_id,
                title=tool_call.tool_name,
                kind=self._tool_classifier.classify(tool_call.tool_name, raw_input),
                locations=extract_tool_call_locations(raw_input),
                status="failed",
                raw_output="Permission request cancelled.",
            ),
        )

    def _build_run_output_type(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> RunOutputType | None:
        if not self._supports_deferred_approval_bridge():
            return None
        output_type = agent.output_type
        if contains_deferred_tool_requests(output_type):
            return output_type
        return [output_type, DeferredToolRequests]

    def _build_run_kwargs(
        self,
        *,
        message_history: list[ModelMessage] | None,
        deferred_tool_results: DeferredToolResults | None,
        deps: AgentDepsT | None,
        model_override: ModelOverride | None,
        output_type: RunOutputType | None,
    ) -> dict[str, Any]:
        run_kwargs: dict[str, Any] = {
            "message_history": message_history,
            "deferred_tool_results": deferred_tool_results,
            "model": model_override,
        }
        if deps is not None:
            run_kwargs["deps"] = deps
        if output_type is not None:
            run_kwargs["output_type"] = output_type
        return run_kwargs

    def _should_stream_text_responses(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        model_override: ModelOverride | None,
        output_type: RunOutputType | None,
    ) -> bool:
        candidate_output_type = agent.output_type if output_type is None else output_type
        return self._contains_text_output(candidate_output_type) and self._supports_streaming_model(
            agent,
            model_override=model_override,
        )

    def _contains_text_output(self, output_type: object) -> bool:
        if output_type is str:
            return True
        if isinstance(output_type, Sequence) and not isinstance(output_type, str):
            return any(self._contains_text_output(item) for item in output_type)
        return False

    def _supports_streaming_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        model_override: ModelOverride | None,
    ) -> bool:
        model = self._resolve_runtime_model(agent, model_override=model_override)
        if isinstance(model, FunctionModel):
            return model.stream_function is not None
        return type(model).request_stream is not pydantic_models.Model.request_stream

    def _resolve_runtime_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        model_override: ModelOverride | None,
    ) -> pydantic_models.Model:
        if model_override is None:
            model = agent.model
            if isinstance(model, pydantic_models.Model):
                return model
            if isinstance(model, str):
                try:
                    return pydantic_models.infer_model(model)
                except ValueError as exc:
                    raise UserError(str(exc)) from exc
            raise RequestError.internal_error({"reason": "agent_model_missing"})
        if isinstance(model_override, pydantic_models.Model):
            return model_override
        try:
            return pydantic_models.infer_model(model_override)
        except ValueError as exc:
            raise UserError(str(exc)) from exc

    def _text_chunk_from_event(
        self,
        event: object,
    ) -> str | None:
        if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
            return event.part.content
        if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
            return event.delta.content_delta
        return None

    def _model_identity(self, model_value: object) -> str | None:
        if isinstance(model_value, str):
            return model_value
        model_name = getattr(model_value, "model_name", None)
        return model_name if isinstance(model_name, str) else None

    async def _handle_slash_command(
        self,
        command_name: str,
        *,
        argument: str | None,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> str | None:
        if command_name == "model":
            if argument is None:
                return render_model_message(self._resolve_current_model_id(session, agent))
            self._set_agent_model(agent, session, argument)
            surface = await self._build_session_surface(session, agent)
            await self._emit_session_state_updates(
                session,
                surface,
                emit_available_commands=True,
                emit_config_options=True,
                emit_current_mode=False,
                emit_plan=True,
                emit_session_info=True,
            )
            return render_model_message(self._resolve_current_model_id(session, agent))
        if command_name == "tools":
            return render_tool_listing(list_agent_tools(agent))
        if command_name == "hooks":
            return render_hook_listing(
                list_agent_hooks(agent),
                projection_map=self._config.hook_projection_map,
            )
        if command_name == "mcp-servers":
            return render_mcp_server_listing(
                extract_session_mcp_servers(session, agent=agent),
            )
        return None

    def _apply_session_model_to_agent(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        session: AcpSessionContext,
    ) -> None:
        session_model_id = session.session_model_id
        if session_model_id is None:
            if isinstance(getattr(agent, "_acp_selected_model_id", None), str):
                self._restore_agent_default_model(agent)
            return
        if getattr(agent, "_acp_selected_model_id", None) == session_model_id:
            return
        self._set_agent_model(agent, session, session_model_id)

    def _set_agent_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        session: AcpSessionContext,
        model_id: str,
    ) -> None:
        self._remember_default_model(agent)
        selected_model = self._resolve_selected_model(model_id)
        agent.model = selected_model
        agent._acp_selected_model_id = model_id.strip()  # type: ignore
        session.session_model_id = model_id.strip()
        session.config_values["model"] = model_id.strip()

    def _restore_default_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        session: AcpSessionContext,
    ) -> bool:
        if session.session_model_id is None:
            return False
        if not self._restore_agent_default_model(agent):
            return False
        default_model = getattr(agent, "_acp_default_model", None)
        restored_model_id = self._resolve_model_id_from_value(default_model)
        session.session_model_id = restored_model_id
        if restored_model_id is None:
            session.config_values.pop("model", None)
        else:
            session.config_values["model"] = restored_model_id
        return True

    def _restore_agent_default_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> bool:
        default_model = getattr(agent, "_acp_default_model", None)
        if default_model is None:
            return False
        agent.model = default_model
        agent._acp_selected_model_id = None  # type: ignore
        return True

    def _remember_default_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        if getattr(agent, "_acp_default_model", None) is not None:
            return
        agent._acp_default_model = agent.model  # type: ignore

    def _resolve_selected_model(self, model_id: str) -> ModelOverride:
        normalized_model_id = model_id.strip()
        if not normalized_model_id:
            raise RequestError.invalid_params({"modelId": model_id})
        model_option = self._find_model_option(normalized_model_id)
        if model_option is not None:
            return model_option.override
        if normalized_model_id.startswith("codex:"):
            codex_model_id = normalized_model_id.removeprefix("codex:").strip()
            if not codex_model_id:
                raise RequestError.invalid_params({"modelId": model_id})
            try:
                from codex_auth_helper import create_codex_responses_model
            except ImportError as exc:
                raise RequestError.invalid_params({"modelId": normalized_model_id}) from exc
            return create_codex_responses_model(codex_model_id)
        return normalized_model_id

    def _update_session_mcp_servers(
        self,
        session: AcpSessionContext,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None,
    ) -> None:
        if mcp_servers is None:
            return
        session.mcp_servers = [self._serialize_mcp_server(server) for server in mcp_servers]

    def _serialize_mcp_server(
        self,
        server: HttpMcpServer | McpServerStdio | SseMcpServer,
    ) -> dict[str, JsonValue]:
        if isinstance(server, McpServerStdio):
            return {
                "args": list(server.args),
                "command": server.command,
                "name": server.name,
                "transport": "stdio",
            }
        return {
            "name": server.name,
            "transport": server.type,
            "url": server.url,
        }

    def _require_session(self, session_id: str) -> AcpSessionContext:
        session = self._config.session_store.get(session_id)
        if session is None:
            raise RequestError.invalid_params({"sessionId": session_id})
        return session
