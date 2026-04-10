from __future__ import annotations as _annotations

from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, TypeAlias, TypeVar, get_args

from acp.exceptions import RequestError
from acp.schema import (
    AgentPlanUpdate,
    AvailableCommandsUpdate,
    ConfigOptionUpdate,
    CurrentModeUpdate,
    HttpMcpServer,
    ListSessionsResponse,
    McpServerStdio,
    NewSessionResponse,
    PlanEntry,
    ResumeSessionResponse,
    SessionInfo,
    SessionInfoUpdate,
    SetSessionConfigOptionResponse,
    SetSessionModelResponse,
    SetSessionModeResponse,
    SseMcpServer,
)
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai import models as pydantic_models
from pydantic_ai.output import OutputSpec

from .._slash_commands import validate_mode_command_ids
from ..awaitables import resolve_value
from ..models import AdapterModel, ModelOverride
from ..providers import ModelSelectionState, ModeState
from ..session.state import AcpSessionContext, JsonValue, utc_now
from ._agent_state import (
    assign_model,
    clear_selected_model_id,
    default_model,
    remember_default_model,
    selected_model_id,
    set_active_session,
    set_selected_model_id,
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
    render_hook_listing,
    render_mcp_server_listing,
    render_mode_message,
    render_model_message,
    render_thinking_message,
    render_tool_listing,
)

if TYPE_CHECKING:
    from .adapter import PydanticAcpAgent

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)
OutputDataT = TypeVar("OutputDataT", covariant=True)
RunOutputType: TypeAlias = OutputSpec[Any]

__all__ = ("_SessionRuntime",)


class _SessionRuntime(Generic[AgentDepsT, OutputDataT]):
    def __init__(self, owner: PydanticAcpAgent[AgentDepsT, OutputDataT]) -> None:
        self._owner = owner

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None = None,
    ) -> NewSessionResponse:
        session = self._bind_session_client(
            AcpSessionContext(
                session_id=self._owner._new_session_id(),
                cwd=self._normalize_cwd(cwd),
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        self._update_session_mcp_servers(session, mcp_servers)
        self._owner._config.session_store.save(session)
        agent = await self._owner._agent_source.get_agent(session)
        self._configure_agent_runtime(session, agent)
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
    ) -> NewSessionResponse | None:
        session = self._owner._config.session_store.get(session_id)
        if session is None:
            return None
        session = self._bind_session_client(session)
        session.cwd = self._normalize_cwd(cwd)
        self._update_session_mcp_servers(session, mcp_servers)
        session.updated_at = utc_now()
        self._owner._config.session_store.save(session)
        await self._owner._replay_transcript(session)
        agent = await self._owner._agent_source.get_agent(session)
        self._configure_agent_runtime(session, agent)
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
            config_options=surface.config_options,
            models=surface.model_state,
            modes=surface.mode_state,
            session_id=session.session_id,
        )

    async def list_sessions(
        self,
        *,
        cwd: str | None = None,
    ) -> ListSessionsResponse:
        sessions = self._owner._config.session_store.list_sessions()
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

    async def fork_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None = None,
    ) -> NewSessionResponse:
        forked_session = self._owner._config.session_store.fork(
            session_id,
            new_session_id=self._owner._new_session_id(),
            cwd=self._normalize_cwd(cwd),
        )
        if forked_session is None:
            raise RequestError.invalid_params({"sessionId": session_id})
        forked_session = self._bind_session_client(forked_session)
        self._update_session_mcp_servers(forked_session, mcp_servers)
        agent = await self._owner._agent_source.get_agent(forked_session)
        self._configure_agent_runtime(forked_session, agent)
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
        return NewSessionResponse(
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
    ) -> ResumeSessionResponse:
        session = self._require_session(session_id)
        session.cwd = self._normalize_cwd(cwd)
        self._update_session_mcp_servers(session, mcp_servers)
        session.updated_at = utc_now()
        self._owner._config.session_store.save(session)
        await self._owner._replay_transcript(session)
        agent = await self._owner._agent_source.get_agent(session)
        self._configure_agent_runtime(session, agent)
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

    async def close_session(self, session_id: str) -> bool:
        session = self._owner._config.session_store.get(session_id)
        if session is None:
            return False
        self._owner._config.session_store.delete(session_id)
        return True

    async def set_session_mode(
        self,
        mode_id: str,
        session_id: str,
    ) -> SetSessionModeResponse | None:
        session = self._require_session(session_id)
        agent = await self._owner._agent_source.get_agent(session)
        self._configure_agent_runtime(session, agent)
        mode_state = await self._set_provider_mode_state(session, agent, mode_id)
        if mode_state is None:
            return None
        session.updated_at = utc_now()
        self._owner._config.session_store.save(session)
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
        self,
        model_id: str,
        session_id: str,
    ) -> SetSessionModelResponse | None:
        session = self._require_session(session_id)
        agent = await self._owner._agent_source.get_agent(session)
        self._configure_agent_runtime(session, agent)
        if self._owner._config.models_provider is not None:
            model_state = await self._set_provider_model_state(session, agent, model_id)
            if model_state is None:
                return None
        else:
            model_state = await self._get_model_selection_state(session, agent)
            if model_state is None:
                return None
            model_option = self._find_model_option(
                model_id,
                available_models=model_state.available_models,
            )
            if model_option is None:
                if not model_state.allow_any_model_id:
                    raise RequestError.invalid_params({"modelId": model_id})
                selected_model = self._resolve_unconfigured_model_id(model_id)
            else:
                selected_model = self._resolve_model_option(model_option)
            self._remember_default_model(agent)
            normalized_model_id = model_id.strip()
            agent.model = selected_model
            set_selected_model_id(agent, normalized_model_id)
            session.session_model_id = normalized_model_id
            session.config_values["model"] = normalized_model_id
        session.updated_at = utc_now()
        self._owner._config.session_store.save(session)
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
    ) -> SetSessionConfigOptionResponse | None:
        session = self._require_session(session_id)
        agent = await self._owner._agent_source.get_agent(session)
        self._configure_agent_runtime(session, agent)
        handled = False
        provider_options = await self._get_provider_config_options(session, agent)
        provider_option_ids = {option.id for option in provider_options or []}
        if config_id == "model" and config_id not in provider_option_ids:
            if not isinstance(value, str):
                raise RequestError.invalid_params({"configId": config_id, "value": value})
            if await self.set_session_model(value, session_id) is not None:
                handled = True
        elif config_id == "mode" and config_id not in provider_option_ids:
            if not isinstance(value, str):
                raise RequestError.invalid_params({"configId": config_id, "value": value})
            if await self.set_session_mode(value, session_id) is not None:
                handled = True
        if (
            not handled
            and self._owner._config.config_options_provider is not None
            and await self._set_provider_config_options(
                session,
                agent,
                config_id,
                value,
                current_options=provider_options,
            )
        ):
            session.updated_at = utc_now()
            self._owner._config.session_store.save(session)
            handled = True
        if not handled:
            bridge_options = self._owner._bridge_manager.set_config_option(
                session,
                agent,
                config_id,
                value,
            )
            if bridge_options is not None:
                session.updated_at = utc_now()
                self._owner._config.session_store.save(session)
                handled = True
        if not handled:
            return None
        updated_session = self._require_session(session_id)
        updated_agent = await self._owner._agent_source.get_agent(updated_session)
        self._configure_agent_runtime(updated_session, updated_agent)
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

    def _normalize_cwd(self, cwd: str) -> Path:
        path = Path(cwd).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return path

    def _configure_agent_runtime(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        self._set_active_session(agent, session)
        self._apply_session_model_to_agent(agent, session)
        self._owner._install_native_plan_tools(agent)

    def _set_active_session(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        session: AcpSessionContext,
    ) -> None:
        set_active_session(agent, session)

    def _bind_session_client(self, session: AcpSessionContext) -> AcpSessionContext:
        session.client = self._owner._client
        return session

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
        self._owner._config.session_store.save(session)
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
        client = self._owner._client
        if client is None:
            return
        if emit_session_info and session.metadata:
            await client.session_update(
                session_id=session.session_id,
                update=SessionInfoUpdate(
                    session_update="session_info_update",
                    title=session.title,
                    updated_at=session.updated_at.isoformat(),
                    field_meta=session.metadata or None,
                ),
            )
        if emit_available_commands:
            await client.session_update(
                session_id=session.session_id,
                update=AvailableCommandsUpdate(
                    session_update="available_commands_update",
                    available_commands=build_available_commands(
                        mode_state=surface.mode_state,
                        model_state=surface.model_state,
                        config_options=surface.config_options,
                    ),
                ),
            )
        if emit_current_mode and surface.mode_state is not None:
            await client.session_update(
                session_id=session.session_id,
                update=CurrentModeUpdate(
                    session_update="current_mode_update",
                    current_mode_id=surface.mode_state.current_mode_id,
                ),
            )
        if emit_config_options and surface.config_options is not None:
            await client.session_update(
                session_id=session.session_id,
                update=ConfigOptionUpdate(
                    session_update="config_option_update",
                    config_options=surface.config_options,
                ),
            )
        if emit_plan and surface.plan_entries is not None:
            await client.session_update(
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
        provider_options = await self._get_provider_config_options(session, agent)
        provider_option_ids = {option.id for option in provider_options or []}
        options: list[ConfigOption] = []
        if (
            model_selection_state is not None
            and not model_selection_state.allow_any_model_id
            and model_selection_state.enable_config_option
            and model_selection_state.current_model_id is not None
            and model_selection_state.available_models
            and "model" not in provider_option_ids
        ):
            options.append(build_model_config_option(model_selection_state))
        if (
            mode_state is not None
            and mode_state.current_mode_id is not None
            and mode_state.modes
            and "mode" not in provider_option_ids
        ):
            options.append(build_mode_config_option(mode_state))
        if provider_options is not None:
            options.extend(provider_options)
        bridge_options = self._owner._bridge_manager.get_config_options(session, agent)
        if bridge_options is not None:
            reserved_ids = {option.id for option in options}
            options.extend(option for option in bridge_options if option.id not in reserved_ids)
        return options or None

    async def _get_model_selection_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> ModelSelectionState | None:
        provider = self._owner._config.models_provider
        if provider is not None:
            model_state = await resolve_value(provider.get_model_state(session, agent))
            self._synchronize_session_model_selection(session, model_state)
            return model_state
        current_model_id = self._resolve_current_model_id(session, agent)
        if current_model_id is None:
            return None
        if self._owner._config.available_models:
            return ModelSelectionState(
                available_models=list(self._owner._config.available_models),
                current_model_id=current_model_id,
                enable_config_option=self._owner._config.enable_model_config_option,
            )
        current_model_value = agent.model
        current_model_override: ModelOverride
        if isinstance(current_model_value, pydantic_models.Model | str):
            current_model_override = current_model_value
        else:
            current_model_override = current_model_id
        return ModelSelectionState(
            available_models=_default_available_models(
                current_model_id,
                current_model_value=current_model_override,
            ),
            current_model_id=current_model_id,
            enable_config_option=self._owner._config.enable_model_config_option,
        )

    async def _set_provider_model_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        model_id: str,
    ) -> ModelSelectionState | None:
        provider = self._owner._config.models_provider
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
        provider = self._owner._config.modes_provider
        mode_state: ModeState | None = None
        if provider is not None:
            mode_state = await resolve_value(provider.get_mode_state(session, agent))
        if mode_state is None:
            mode_state = self._owner._bridge_manager.get_mode_state(session, agent)
        self._validate_mode_state(mode_state)
        self._synchronize_mode_state(session, mode_state)
        return mode_state

    async def _set_provider_mode_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        mode_id: str,
    ) -> ModeState | None:
        provider = self._owner._config.modes_provider
        mode_state: ModeState | None = None
        if provider is not None:
            mode_state = await resolve_value(provider.set_mode(session, agent, mode_id))
            if mode_state is None:
                mode_state = await resolve_value(provider.get_mode_state(session, agent))
        if mode_state is None:
            mode_state = self._owner._bridge_manager.set_mode(session, agent, mode_id)
        self._validate_mode_state(mode_state)
        self._synchronize_mode_state(session, mode_state)
        return mode_state

    def _validate_mode_state(self, mode_state: ModeState | None) -> None:
        if mode_state is None:
            return
        validate_mode_command_ids(mode.id for mode in mode_state.modes)

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
        provider = self._owner._config.config_options_provider
        if provider is None:
            return None
        return await resolve_value(provider.get_config_options(session, agent))

    async def _set_provider_config_options(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        config_id: str,
        value: str | bool,
        *,
        current_options: list[ConfigOption] | None = None,
    ) -> bool:
        provider = self._owner._config.config_options_provider
        if provider is None:
            return False
        options = await resolve_value(provider.set_config_option(session, agent, config_id, value))
        if options is not None:
            return True
        if current_options is None:
            current_options = await resolve_value(provider.get_config_options(session, agent))
        if current_options is None:
            return False
        return any(option.id == config_id for option in current_options)

    async def _get_plan_entries(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> list[PlanEntry] | None:
        provider = self._owner._config.plan_provider
        if provider is None:
            return self._owner._get_native_plan_entries(session)
        plan_entries = await resolve_value(provider.get_plan(session, agent))
        return list(plan_entries) if plan_entries is not None else None

    async def _synchronize_session_metadata(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        metadata_sections = self._owner._bridge_manager.get_metadata_sections(session, agent)
        approval_state = await self._get_approval_state(session, agent)
        if approval_state is not None:
            metadata_sections["approval_state"] = approval_state
        plan_storage = self._plan_storage_metadata(session)
        if plan_storage is not None:
            metadata_sections["plan_storage"] = plan_storage
        session.metadata = {"pydantic_acp": metadata_sections} if metadata_sections else {}

    async def _get_approval_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> dict[str, JsonValue] | None:
        provider = self._owner._config.approval_state_provider
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
            available_models=available_models or self._owner._config.available_models,
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
        active_model_id = selected_model_id(agent)
        if active_model_id:
            return active_model_id
        model_value = getattr(agent, "model", None)
        return self._resolve_model_id_from_value(model_value)

    def _resolve_model_id_from_value(self, model_value: ModelOverride | None) -> str | None:
        model_identity = self._model_identity(model_value)
        for model_option in self._owner._config.available_models:
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
            and selected_model_id(agent) == session.session_model_id
        ):
            return None
        model_selection_state = await self._get_model_selection_state(session, agent)
        current_model_id = session.session_model_id
        model_options = self._owner._config.available_models
        if model_selection_state is not None:
            current_model_id = model_selection_state.current_model_id
            model_options = model_selection_state.available_models
        if current_model_id is None:
            return None
        model_option = self._find_model_option(
            current_model_id,
            available_models=model_options,
        )
        if model_option is not None:
            return self._resolve_model_option(model_option)
        return self._resolve_unconfigured_model_id(current_model_id)

    def _supports_fallback_model_selection(self) -> bool:
        return True

    async def _handle_slash_command(
        self,
        command_name: str,
        *,
        argument: str | None,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> str | None:
        mode_state = await self._get_mode_state(session, agent)
        if mode_state is not None and command_name in {mode.id for mode in mode_state.modes}:
            if await self.set_session_mode(command_name, session.session_id) is None:
                return f"Mode `{command_name}` is unavailable"
            return render_mode_message(command_name)
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
        if command_name == "thinking":
            if argument is None:
                return render_thinking_message(self._current_thinking_value(session, agent))
            normalized_argument = argument.strip().lower()
            if (
                await self.set_config_option("thinking", session.session_id, normalized_argument)
                is None
            ):
                return "Thinking effort is unavailable or invalid"
            return render_thinking_message(normalized_argument)
        if command_name == "tools":
            return render_tool_listing(list_agent_tools(agent))
        if command_name == "hooks":
            return render_hook_listing(
                self._owner._list_agent_hooks(agent),
                projection_map=self._owner._config.hook_projection_map,
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
            if selected_model_id(agent) is not None:
                self._restore_agent_default_model(agent)
            return
        if selected_model_id(agent) == session_model_id:
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
        assign_model(agent, selected_model)
        normalized_model_id = model_id.strip()
        set_selected_model_id(agent, normalized_model_id)
        session.session_model_id = normalized_model_id
        session.config_values["model"] = normalized_model_id

    def _restore_default_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        session: AcpSessionContext,
    ) -> bool:
        if session.session_model_id is None:
            return False
        if not self._restore_agent_default_model(agent):
            return False
        restored_model_id = self._resolve_model_id_from_value(default_model(agent))
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
        agent_default_model = default_model(agent)
        if agent_default_model is None:
            return False
        assign_model(agent, agent_default_model)
        clear_selected_model_id(agent)
        return True

    def _remember_default_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        remember_default_model(agent)

    def _resolve_selected_model(self, model_id: str) -> ModelOverride:
        normalized_model_id = model_id.strip()
        if not normalized_model_id:
            raise RequestError.invalid_params({"modelId": model_id})
        model_option = self._find_model_option(normalized_model_id)
        if model_option is not None:
            return self._resolve_model_option(model_option)
        return self._resolve_unconfigured_model_id(normalized_model_id)

    def _resolve_model_option(self, model_option: AdapterModel) -> ModelOverride:
        if (
            isinstance(model_option.override, str)
            and model_option.override.strip() == model_option.model_id.strip()
        ):
            return self._resolve_unconfigured_model_id(model_option.model_id)
        return model_option.override

    def _resolve_unconfigured_model_id(self, model_id: str) -> ModelOverride:
        normalized_model_id = model_id.strip()
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

    def _plan_storage_metadata(self, session: AcpSessionContext) -> dict[str, JsonValue] | None:
        if (
            self._owner._config.plan_provider is None
            and self._owner._native_plan_bridge(session) is None
        ):
            return None
        return {"directory": str(session.cwd / ".acpkit" / "plans")}

    def _current_thinking_value(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> str | None:
        bridge_options = self._owner._bridge_manager.get_config_options(session, agent)
        if bridge_options is None:
            return None
        for option in bridge_options:
            if option.id == "thinking" and isinstance(option.current_value, str):
                return option.current_value
        return None

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
        session = self._owner._config.session_store.get(session_id)
        if session is None:
            raise RequestError.invalid_params({"sessionId": session_id})
        return self._bind_session_client(session)

    def _model_identity(self, model_value: ModelOverride | None) -> str | None:
        if isinstance(model_value, str):
            return model_value
        model_name = getattr(model_value, "model_name", None)
        return model_name if isinstance(model_name, str) else None


@lru_cache(maxsize=1)
def _known_pydantic_model_ids() -> tuple[str, ...]:
    return tuple(
        model_id
        for model_id in get_args(pydantic_models.KnownModelName.__value__)
        if isinstance(model_id, str) and model_id != "test"
    )


def _known_codex_model_ids() -> tuple[str, ...]:
    return (
        "codex:gpt-5.4",
        "codex:gpt-5.4-mini",
        "codex:gpt-5.3-codex",
        "codex:gpt-5.2",
    )


def _default_available_models(
    current_model_id: str,
    *,
    current_model_value: ModelOverride,
) -> list[AdapterModel]:
    available_models: list[AdapterModel] = []
    seen_model_ids: set[str] = set()

    def add_model(model_id: str, *, override: ModelOverride | None = None) -> None:
        normalized_model_id = model_id.strip()
        if not normalized_model_id or normalized_model_id in seen_model_ids:
            return
        seen_model_ids.add(normalized_model_id)
        available_models.append(
            AdapterModel(
                model_id=normalized_model_id,
                name=normalized_model_id,
                override=normalized_model_id if override is None else override,
            )
        )

    add_model(current_model_id, override=current_model_value)
    for model_id in _known_pydantic_model_ids():
        add_model(model_id)
    for model_id in _known_codex_model_ids():
        add_model(model_id)
    return available_models
