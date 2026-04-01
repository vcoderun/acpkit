from __future__ import annotations as _annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, Generic, TypeAlias, TypeVar, cast
from uuid import uuid4

from acp import PROTOCOL_VERSION
from acp.exceptions import RequestError
from acp.interfaces import Client as AcpClient
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    AgentPlanUpdate,
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
)
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.messages import ModelMessage, ToolCallPart
from pydantic_ai.output import OutputSpec
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults

from ..agent_source import AgentSource
from ..approvals import ApprovalResolution
from ..awaitables import resolve_value
from ..config import AdapterConfig
from ..models import AdapterModel, ModelOverride
from ..projection import build_tool_updates, extract_tool_call_locations
from ..providers import ModelSelectionState, ModeState
from ..session.state import (
    AcpSessionContext,
    JsonValue,
    SessionTranscriptUpdate,
    StoredSessionUpdate,
    utc_now,
)
from .bridge_manager import BridgeManager
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
        del mcp_servers, kwargs
        session = AcpSessionContext(
            session_id=uuid4().hex,
            cwd=self._normalize_cwd(cwd),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self._config.session_store.save(session)
        agent = await self._agent_source.get_agent(session)
        surface = await self._build_session_surface(session, agent)
        await self._emit_session_state_updates(
            session,
            surface,
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
        del mcp_servers, kwargs
        session = self._config.session_store.get(session_id)
        if session is None:
            return None
        session.cwd = self._normalize_cwd(cwd)
        session.updated_at = utc_now()
        self._config.session_store.save(session)
        await self._replay_transcript(session)
        agent = await self._agent_source.get_agent(session)
        surface = await self._build_session_surface(session, agent)
        await self._emit_session_state_updates(
            session,
            surface,
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

        if session.title is None:
            session.title = derive_title(prompt)
        session.updated_at = utc_now()
        self._config.session_store.save(session)

        agent = await self._agent_source.get_agent(session)
        prompt_outcome = await self._run_prompt(agent=agent, prompt=prompt, session=session)
        result = prompt_outcome.result

        output_text = ""
        if prompt_outcome.stop_reason != "cancelled":
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
        del mcp_servers, kwargs
        forked_session = self._config.session_store.fork(
            session_id,
            new_session_id=uuid4().hex,
            cwd=self._normalize_cwd(cwd),
        )
        if forked_session is None:
            raise RequestError.invalid_params({"sessionId": session_id})
        agent = await self._agent_source.get_agent(forked_session)
        surface = await self._build_session_surface(forked_session, agent)
        await self._emit_session_state_updates(
            forked_session,
            surface,
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
        del mcp_servers, kwargs
        session = self._require_session(session_id)
        session.cwd = self._normalize_cwd(cwd)
        session.updated_at = utc_now()
        self._config.session_store.save(session)
        await self._replay_transcript(session)
        agent = await self._agent_source.get_agent(session)
        surface = await self._build_session_surface(session, agent)
        await self._emit_session_state_updates(
            session,
            surface,
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
            self._require_model_option(model_id)
            session.session_model_id = model_id
            session.config_values["model"] = model_id
        session.updated_at = utc_now()
        self._config.session_store.save(session)
        surface = await self._build_session_surface(session, agent)
        await self._emit_session_state_updates(
            session,
            surface,
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
            result = await agent.run(
                prompt_text,
                message_history=message_history,
                deferred_tool_results=deferred_tool_results,
                deps=cast(AgentDepsT, None),
                model=await self._resolve_model_override(session, agent),
                output_type=self._build_run_output_type(agent),
            )
            await self._record_tool_updates(session, agent, result.new_messages())

            if not isinstance(result.output, DeferredToolRequests):
                return PromptRunOutcome(result=result, stop_reason="end_turn")
            if not self._supports_deferred_approval_bridge():
                return PromptRunOutcome(result=result, stop_reason="end_turn")
            if result.output.calls or not result.output.approvals:
                return PromptRunOutcome(result=result, stop_reason="end_turn")

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
                return PromptRunOutcome(result=result, stop_reason="cancelled")

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
            serializer=self._config.output_serializer,
        ):
            await self._record_update(session, update)
        for update in self._bridge_manager.drain_updates(session, agent):
            await self._record_update(session, update)

    def _normalize_cwd(self, cwd: str) -> Path:
        path = Path(cwd).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return path

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
        if not self._supports_fallback_model_selection():
            return None
        current_model_id = self._resolve_current_model_id(session, agent)
        if current_model_id is None:
            return None
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
        if isinstance(model_value, str) and self._find_model_option(model_value) is not None:
            return model_value
        return None

    async def _resolve_model_override(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> ModelOverride | None:
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
        output_type = getattr(agent, "output_type", None)
        if output_type is None:
            return DeferredToolRequests
        if contains_deferred_tool_requests(output_type):
            return cast(RunOutputType, output_type)
        return cast(RunOutputType, [cast(RunOutputType, output_type), DeferredToolRequests])

    def _model_identity(self, model_value: object) -> str | None:
        if isinstance(model_value, str):
            return model_value
        model_name = getattr(model_value, "model_name", None)
        return model_name if isinstance(model_name, str) else None

    def _require_session(self, session_id: str) -> AcpSessionContext:
        session = self._config.session_store.get(session_id)
        if session is None:
            raise RequestError.invalid_params({"sessionId": session_id})
        return session
