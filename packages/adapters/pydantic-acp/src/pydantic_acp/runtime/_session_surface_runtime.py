from __future__ import annotations as _annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Generic, TypeVar

from acp.schema import (
    AgentPlanUpdate,
    AvailableCommandsUpdate,
    ConfigOptionUpdate,
    CurrentModeUpdate,
    PlanEntry,
    SessionInfoUpdate,
)
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai import models as pydantic_models

from .._slash_commands import validate_mode_command_ids
from ..awaitables import resolve_value
from ..models import AdapterModel, ModelOverride
from ..providers import ModelSelectionState, ModeState
from ..session.state import AcpSessionContext, JsonValue
from ._agent_state import selected_model_id
from .session_surface import (
    ConfigOption,
    SessionSurface,
    build_mode_config_option,
    build_mode_state_from_selection,
    build_model_config_option,
    build_model_state_from_selection,
    find_model_option,
)
from .slash_commands import build_available_commands

if TYPE_CHECKING:
    from ._session_runtime import _SessionRuntime

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)
OutputDataT = TypeVar("OutputDataT", covariant=True)

__all__ = ("_SessionSurfaceRuntime",)


class _SessionSurfaceRuntime(Generic[AgentDepsT, OutputDataT]):
    def __init__(self, runtime: _SessionRuntime[AgentDepsT, OutputDataT]) -> None:
        self._runtime = runtime

    async def build_session_surface(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> SessionSurface:
        model_selection_state = await self.get_model_selection_state(session, agent)
        mode_state = await self.get_mode_state(session, agent)
        config_options = await self.build_config_options(
            session,
            agent,
            model_selection_state=model_selection_state,
            mode_state=mode_state,
        )
        surface = SessionSurface(
            config_options=config_options,
            model_state=build_model_state_from_selection(model_selection_state),
            mode_state=build_mode_state_from_selection(mode_state),
            plan_entries=await self.get_plan_entries(session, agent),
        )
        await self.synchronize_session_metadata(session, agent)
        self._runtime._owner._config.session_store.save(session)
        return surface

    async def emit_session_state_updates(
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
        client = self._runtime._owner._client
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

    async def build_config_options(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        model_selection_state: ModelSelectionState | None,
        mode_state: ModeState | None,
    ) -> list[ConfigOption] | None:
        provider_options = await self.get_provider_config_options(session, agent)
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
        bridge_options = self._runtime._owner._bridge_manager.get_config_options(session, agent)
        if bridge_options is not None:
            reserved_ids = {option.id for option in options}
            options.extend(option for option in bridge_options if option.id not in reserved_ids)
        return options or None

    async def get_model_selection_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> ModelSelectionState | None:
        provider = self._runtime._owner._config.models_provider
        if provider is not None:
            model_state = await resolve_value(provider.get_model_state(session, agent))
            self.synchronize_session_model_selection(session, model_state)
            return model_state
        current_model_id = self._runtime._resolve_current_model_id(session, agent)
        if current_model_id is None:
            return None
        if self._runtime._owner._config.available_models:
            return ModelSelectionState(
                available_models=list(self._runtime._owner._config.available_models),
                current_model_id=current_model_id,
                enable_config_option=self._runtime._owner._config.enable_model_config_option,
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
            enable_config_option=self._runtime._owner._config.enable_model_config_option,
        )

    async def set_provider_model_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        model_id: str,
    ) -> ModelSelectionState | None:
        provider = self._runtime._owner._config.models_provider
        if provider is None:
            return None
        model_state = await resolve_value(provider.set_model(session, agent, model_id))
        if model_state is None:
            model_state = await resolve_value(provider.get_model_state(session, agent))
        self.synchronize_session_model_selection(session, model_state)
        return model_state

    def synchronize_session_model_selection(
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

    async def get_mode_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> ModeState | None:
        provider = self._runtime._owner._config.modes_provider
        mode_state: ModeState | None = None
        if provider is not None:
            mode_state = await resolve_value(provider.get_mode_state(session, agent))
        if mode_state is None:
            mode_state = self._runtime._owner._bridge_manager.get_mode_state(session, agent)
        self.validate_mode_state(mode_state)
        self.synchronize_mode_state(session, mode_state)
        return mode_state

    async def set_provider_mode_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        mode_id: str,
    ) -> ModeState | None:
        provider = self._runtime._owner._config.modes_provider
        mode_state: ModeState | None = None
        if provider is not None:
            mode_state = await resolve_value(provider.set_mode(session, agent, mode_id))
            if mode_state is None:
                mode_state = await resolve_value(provider.get_mode_state(session, agent))
        if mode_state is None:
            mode_state = self._runtime._owner._bridge_manager.set_mode(session, agent, mode_id)
        self.validate_mode_state(mode_state)
        self.synchronize_mode_state(session, mode_state)
        return mode_state

    def validate_mode_state(self, mode_state: ModeState | None) -> None:
        if mode_state is None:
            return
        validate_mode_command_ids(mode.id for mode in mode_state.modes)

    def synchronize_mode_state(
        self,
        session: AcpSessionContext,
        mode_state: ModeState | None,
    ) -> None:
        if mode_state is None or mode_state.current_mode_id is None:
            session.config_values.pop("mode", None)
            return
        session.config_values["mode"] = mode_state.current_mode_id

    async def get_provider_config_options(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> list[ConfigOption] | None:
        provider = self._runtime._owner._config.config_options_provider
        if provider is None:
            return None
        return await resolve_value(provider.get_config_options(session, agent))

    async def set_provider_config_options(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        config_id: str,
        value: str | bool,
        *,
        current_options: list[ConfigOption] | None = None,
    ) -> bool:
        provider = self._runtime._owner._config.config_options_provider
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

    async def get_plan_entries(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> list[PlanEntry] | None:
        provider = self._runtime._owner._config.plan_provider
        if provider is None:
            return self._runtime._owner._get_native_plan_entries(session)
        plan_entries = await resolve_value(provider.get_plan(session, agent))
        return list(plan_entries) if plan_entries is not None else None

    async def synchronize_session_metadata(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        metadata_sections = self._runtime._owner._bridge_manager.get_metadata_sections(
            session, agent
        )
        approval_state = await self.get_approval_state(session, agent)
        if approval_state is not None:
            metadata_sections["approval_state"] = approval_state
        plan_storage = self.plan_storage_metadata(session)
        if plan_storage is not None:
            metadata_sections["plan_storage"] = plan_storage
        session.metadata = {"pydantic_acp": metadata_sections} if metadata_sections else {}

    async def get_approval_state(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> dict[str, JsonValue] | None:
        provider = self._runtime._owner._config.approval_state_provider
        if provider is None:
            return None
        return await resolve_value(provider.get_approval_state(session, agent))

    def current_thinking_value(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> str | None:
        bridge_options = self._runtime._owner._bridge_manager.get_config_options(session, agent)
        if bridge_options is None:
            return None
        for option in bridge_options:
            if option.id == "thinking" and isinstance(option.current_value, str):
                return option.current_value
        return None

    def plan_storage_metadata(self, session: AcpSessionContext) -> dict[str, JsonValue] | None:
        if (
            self._runtime._owner._config.plan_provider is None
            and self._runtime._owner._native_plan_bridge(session) is None
        ):
            return None
        return {"directory": str(session.cwd / ".acpkit" / "plans")}

    def find_model_option(
        self,
        model_id: str,
        *,
        available_models: Sequence[AdapterModel] | None = None,
    ) -> AdapterModel | None:
        return find_model_option(
            model_id,
            available_models=available_models or self._runtime._owner._config.available_models,
        )

    def require_model_option(self, model_id: str) -> AdapterModel:
        model_option = self.find_model_option(model_id)
        if model_option is None:
            raise ValueError(model_id)
        return model_option

    def resolve_model_id_from_value(self, model_value: ModelOverride | None) -> str | None:
        model_identity = self._runtime._model_identity(model_value)
        for model_option in self._runtime._owner._config.available_models:
            if model_option.override is model_value:
                return model_option.model_id
            if model_identity is not None and model_identity == self._runtime._model_identity(
                model_option.override
            ):
                return model_option.model_id
        if model_identity is not None:
            return model_identity
        if isinstance(model_value, str) and model_value:
            return model_value
        return None

    async def resolve_model_override(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> ModelOverride | None:
        if (
            session.session_model_id is not None
            and selected_model_id(agent) == session.session_model_id
        ):
            return None
        model_selection_state = await self.get_model_selection_state(session, agent)
        current_model_id = session.session_model_id
        model_options = self._runtime._owner._config.available_models
        if model_selection_state is not None:
            current_model_id = model_selection_state.current_model_id
            model_options = model_selection_state.available_models
        if current_model_id is None:
            return None
        model_option = self.find_model_option(
            current_model_id,
            available_models=model_options,
        )
        if model_option is not None:
            return self._runtime._resolve_model_option(model_option)
        return self._runtime._resolve_unconfigured_model_id(current_model_id)


def _known_pydantic_model_ids() -> tuple[str, ...]:
    from ._session_runtime import _known_pydantic_model_ids as load_known_pydantic_model_ids

    return load_known_pydantic_model_ids()


def _known_codex_model_ids() -> tuple[str, ...]:
    from ._session_runtime import _known_codex_model_ids as load_known_codex_model_ids

    return load_known_codex_model_ids()


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
