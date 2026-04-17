from __future__ import annotations as _annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Generic, TypeAlias, TypeVar

from acp.schema import (
    HttpMcpServer,
    ListSessionsResponse,
    LoadSessionResponse,
    McpServerStdio,
    NewSessionResponse,
    PlanEntry,
    PromptResponse,
    ResumeSessionResponse,
    SetSessionConfigOptionResponse,
    SetSessionModelResponse,
    SetSessionModeResponse,
    SseMcpServer,
    ToolCallStart,
)
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai import AgentRunResult
from pydantic_ai import models as pydantic_models
from pydantic_ai.messages import ModelMessage, ToolCallPart
from pydantic_ai.output import OutputSpec
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults

from ..approvals import ApprovalResolution
from ..awaitables import resolve_value
from ..config import AdapterConfig
from ..models import AdapterModel, ModelOverride
from ..providers import ModelSelectionState, ModeState
from ..session.state import AcpSessionContext, JsonValue, SessionTranscriptUpdate
from ._prompt_runtime import _PromptRuntime
from ._session_runtime import _SessionRuntime
from .prompts import PromptBlock, PromptInput, PromptRunOutcome
from .session_surface import ConfigOption, SessionSurface

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)
OutputDataT = TypeVar("OutputDataT", covariant=True)

RunOutputType: TypeAlias = OutputSpec[Any]

__all__ = ("_PromptRuntimeDelegationMixin", "_SessionRuntimeDelegationMixin")


class _PromptRuntimeDelegationMixin(Generic[AgentDepsT, OutputDataT]):
    """Keep prompt-runtime delegation out of the public adapter surface."""

    _config: AdapterConfig
    _prompt_runtime: _PromptRuntime[AgentDepsT, OutputDataT]

    async def _record_update(
        self,
        session: AcpSessionContext,
        update: SessionTranscriptUpdate,
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
        prompt_input: PromptInput | None,
        run_kwargs: dict[str, Any],
        session: AcpSessionContext,
    ) -> tuple[AgentRunResult[Any], bool]:
        return await self._prompt_runtime._run_prompt_with_events(
            agent=agent,
            prompt_input=prompt_input,
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

    async def _resolve_prompt_model_override(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        prompt: Sequence[PromptBlock],
        model_override: ModelOverride | None,
    ) -> ModelOverride | None:
        provider = self._config.prompt_model_override_provider
        if provider is None:
            return model_override
        override = await resolve_value(
            provider.get_prompt_model_override(
                session,
                agent,
                prompt,
                model_override,
            )
        )
        if override is None:
            return model_override
        return override


class _SessionRuntimeDelegationMixin(Generic[AgentDepsT, OutputDataT]):
    """Keep session/runtime plumbing out of the main adapter class body."""

    _adapter_prompt: Any
    _session_runtime: _SessionRuntime[AgentDepsT, OutputDataT]

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

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        """Create a new ACP session rooted at the provided working directory."""
        del kwargs
        return await self._session_runtime.new_session(cwd, mcp_servers)

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse | None:
        """Load a persisted ACP session and rebuild its visible surface."""
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
        """List persisted sessions visible from the current runtime."""
        del cursor, kwargs
        return await self._session_runtime.list_sessions(cwd=cwd)

    async def resume_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        """Resume a session and replay the ACP-visible session surface."""
        del kwargs
        return await self._session_runtime.resume_session(cwd, session_id, mcp_servers)

    async def set_session_mode(
        self, mode_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModeResponse | None:
        """Set the current ACP session mode through the active mode provider."""
        del kwargs
        return await self._session_runtime.set_session_mode(mode_id, session_id)

    async def set_session_model(
        self, model_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModelResponse | None:
        """Set the current ACP session model selection."""
        del kwargs
        return await self._session_runtime.set_session_model(model_id, session_id)

    async def set_config_option(
        self,
        config_id: str,
        session_id: str,
        value: str | bool,
        **kwargs: Any,
    ) -> SetSessionConfigOptionResponse | None:
        """Apply a session-local config option exposed by the adapter or a bridge."""
        del kwargs
        return await self._session_runtime.set_config_option(config_id, session_id, value)

    async def prompt(
        self,
        prompt: list[PromptBlock],
        session_id: str,
        message_id: str | None = None,
        **kwargs: Any,
    ) -> PromptResponse:
        """Run one ACP prompt turn against the bound Pydantic AI agent."""
        del kwargs
        return await self._adapter_prompt.prompt(
            prompt,
            session_id,
            message_id=message_id,
        )
