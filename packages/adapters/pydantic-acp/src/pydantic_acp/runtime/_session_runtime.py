from __future__ import annotations as _annotations

from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, TypeAlias, TypeVar, get_args

from acp.exceptions import RequestError
from acp.schema import (
    HttpMcpServer,
    ListSessionsResponse,
    McpServerStdio,
    NewSessionResponse,
    PlanEntry,
    ResumeSessionResponse,
    SessionInfo,
    SetSessionConfigOptionResponse,
    SetSessionModelResponse,
    SetSessionModeResponse,
    SseMcpServer,
)
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai import models as pydantic_models
from pydantic_ai.output import OutputSpec

from ..models import AdapterModel, ModelOverride
from ..providers import ModelSelectionState, ModeState
from ..session.state import AcpSessionContext, JsonValue, utc_now
from ._agent_state import (
    selected_model_id,
    set_active_session,
    set_selected_model_id,
)
from ._session_lifecycle import _SessionLifecycle
from ._session_model_runtime import _SessionModelRuntime
from ._session_surface_runtime import _SessionSurfaceRuntime
from .session_surface import (
    ConfigOption,
    SessionSurface,
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
        self._lifecycle = _SessionLifecycle(self)
        self._model_runtime = _SessionModelRuntime(self)
        self._surface_runtime = _SessionSurfaceRuntime(self)

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None = None,
    ) -> NewSessionResponse:
        return await self._lifecycle.new_session(cwd, mcp_servers)

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None = None,
    ) -> NewSessionResponse | None:
        return await self._lifecycle.load_session(cwd, session_id, mcp_servers)

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
        return await self._lifecycle.fork_session(cwd, session_id, mcp_servers)

    async def resume_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None = None,
    ) -> ResumeSessionResponse:
        return await self._lifecycle.resume_session(cwd, session_id, mcp_servers)

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
        return await self._surface_runtime.build_session_surface(session, agent)

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
        await self._surface_runtime.emit_session_state_updates(
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
        return await self._surface_runtime.build_config_options(
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
        return await self._surface_runtime.get_model_selection_state(session, agent)

    async def _set_provider_model_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        model_id: str,
    ) -> ModelSelectionState | None:
        return await self._surface_runtime.set_provider_model_state(session, agent, model_id)

    def _synchronize_session_model_selection(
        self,
        session: AcpSessionContext,
        model_state: ModelSelectionState | None,
    ) -> None:
        self._surface_runtime.synchronize_session_model_selection(session, model_state)

    async def _get_mode_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> ModeState | None:
        return await self._surface_runtime.get_mode_state(session, agent)

    async def _set_provider_mode_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        mode_id: str,
    ) -> ModeState | None:
        return await self._surface_runtime.set_provider_mode_state(session, agent, mode_id)

    def _validate_mode_state(self, mode_state: ModeState | None) -> None:
        self._surface_runtime.validate_mode_state(mode_state)

    def _synchronize_mode_state(
        self,
        session: AcpSessionContext,
        mode_state: ModeState | None,
    ) -> None:
        self._surface_runtime.synchronize_mode_state(session, mode_state)

    async def _get_provider_config_options(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> list[ConfigOption] | None:
        return await self._surface_runtime.get_provider_config_options(session, agent)

    async def _set_provider_config_options(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        config_id: str,
        value: str | bool,
        *,
        current_options: list[ConfigOption] | None = None,
    ) -> bool:
        return await self._surface_runtime.set_provider_config_options(
            session,
            agent,
            config_id,
            value,
            current_options=current_options,
        )

    async def _get_plan_entries(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> list[PlanEntry] | None:
        return await self._surface_runtime.get_plan_entries(session, agent)

    async def _synchronize_session_metadata(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        await self._surface_runtime.synchronize_session_metadata(session, agent)

    async def _get_approval_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> dict[str, JsonValue] | None:
        return await self._surface_runtime.get_approval_state(session, agent)

    def _find_model_option(
        self,
        model_id: str,
        *,
        available_models: Sequence[AdapterModel] | None = None,
    ) -> AdapterModel | None:
        return self._surface_runtime.find_model_option(
            model_id,
            available_models=available_models,
        )

    def _require_model_option(self, model_id: str) -> AdapterModel:
        try:
            return self._surface_runtime.require_model_option(model_id)
        except ValueError as exc:
            raise RequestError.invalid_params({"modelId": model_id}) from exc

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
        return self._surface_runtime.resolve_model_id_from_value(model_value)

    async def _resolve_model_override(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> ModelOverride | None:
        return await self._surface_runtime.resolve_model_override(session, agent)

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
        return await self._model_runtime.handle_slash_command(
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
        self._model_runtime.apply_session_model_to_agent(agent, session)

    def _set_agent_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        session: AcpSessionContext,
        model_id: str,
    ) -> None:
        self._model_runtime.set_agent_model(agent, session, model_id)

    def _restore_default_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        session: AcpSessionContext,
    ) -> bool:
        return self._model_runtime.restore_default_model(agent, session)

    def _restore_agent_default_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> bool:
        return self._model_runtime.restore_agent_default_model(agent)

    def _remember_default_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        self._model_runtime.remember_default_model(agent)

    def _resolve_selected_model(self, model_id: str) -> ModelOverride:
        return self._model_runtime.resolve_selected_model(model_id)

    def _resolve_model_option(self, model_option: AdapterModel) -> ModelOverride:
        return self._model_runtime.resolve_model_option(model_option)

    def _resolve_unconfigured_model_id(self, model_id: str) -> ModelOverride:
        return self._model_runtime.resolve_unconfigured_model_id(model_id)

    def _plan_storage_metadata(self, session: AcpSessionContext) -> dict[str, JsonValue] | None:
        return self._surface_runtime.plan_storage_metadata(session)

    def _current_thinking_value(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> str | None:
        return self._surface_runtime.current_thinking_value(session, agent)

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
