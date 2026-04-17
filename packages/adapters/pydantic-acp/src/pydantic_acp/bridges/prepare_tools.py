from __future__ import annotations as _annotations

from dataclasses import dataclass
from typing import Final, Generic, Literal, TypeVar, cast

from acp.schema import SessionConfigOptionSelect, SessionConfigSelectOption, SessionMode
from pydantic_ai.capabilities import PrepareTools
from pydantic_ai.tools import RunContext, ToolDefinition, ToolsPrepareFunc

from .._slash_commands import validate_mode_command_ids
from ..agent_types import RuntimeAgent
from ..awaitables import resolve_value
from ..providers import ConfigOption, ModeState
from ..session.state import AcpSessionContext, JsonValue
from .base import BufferedCapabilityBridge

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)
PlanGenerationType = Literal["tools", "structured"]

_PLAN_GENERATION_CONFIG_OPTIONS: Final[tuple[PlanGenerationType, ...]] = ("tools", "structured")

__all__ = (
    "PlanGenerationType",
    "PrepareToolsBridge",
    "PrepareToolsMode",
)


@dataclass(slots=True, frozen=True, kw_only=True)
class PrepareToolsMode(Generic[AgentDepsT]):
    id: str
    name: str
    prepare_func: ToolsPrepareFunc[AgentDepsT]
    description: str | None = None
    plan_mode: bool = False
    plan_tools: bool = False


@dataclass(slots=True, kw_only=True)
class PrepareToolsBridge(BufferedCapabilityBridge, Generic[AgentDepsT]):
    metadata_key: str | None = "prepare_tools"
    default_mode_id: str
    modes: list[PrepareToolsMode[AgentDepsT]]
    mode_config_key: str = "mode"
    plan_generation_config_id: str = "plan_generation_type"
    plan_generation_config_name: str = "Plan Generation"
    plan_generation_config_description: str = "How plan mode records ACP plan state."
    default_plan_generation_type: PlanGenerationType = "structured"

    def __post_init__(self) -> None:
        if not self.modes:
            raise ValueError("PrepareToolsBridge requires at least one mode.")
        validate_mode_command_ids(mode.id for mode in self.modes)
        mode_ids = {mode.id for mode in self.modes}
        if self.default_mode_id not in mode_ids:
            raise ValueError("PrepareToolsBridge default mode must match one of the modes.")
        if sum(1 for mode in self.modes if mode.plan_mode) > 1:
            raise ValueError("PrepareToolsBridge supports at most one `plan_mode=True` mode.")
        if self.default_plan_generation_type not in _PLAN_GENERATION_CONFIG_OPTIONS:
            raise ValueError("PrepareToolsBridge default plan generation type is invalid.")

    def build_prepare_tools(self, session: AcpSessionContext) -> ToolsPrepareFunc[AgentDepsT]:
        async def prepare_tools(
            ctx: RunContext[AgentDepsT],
            tool_defs: list[ToolDefinition],
        ) -> list[ToolDefinition]:
            mode = self._require_mode(self._current_mode_id(session))
            try:
                prepared = mode.prepare_func(ctx, list(tool_defs))
                resolved = await resolve_value(prepared)
            except Exception as error:
                self._record_failed_event(
                    session,
                    title=f"prepare_tools.{mode.id}",
                    raw_output=str(error),
                )
                raise

            next_tool_defs = list(tool_defs if resolved is None else resolved)
            self._record_completed_event(
                session,
                title=f"prepare_tools.{mode.id}",
                raw_output=f"tools={len(next_tool_defs)}/{len(tool_defs)}",
            )
            return next_tool_defs

        return prepare_tools

    def build_capability(self, session: AcpSessionContext) -> PrepareTools[AgentDepsT]:
        return PrepareTools(self.build_prepare_tools(session))

    def build_agent_capabilities(
        self,
        session: AcpSessionContext,
    ) -> tuple[PrepareTools[AgentDepsT], ...]:
        return (self.build_capability(session),)

    def get_mode_state(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> ModeState:
        del agent
        return ModeState(
            modes=[
                SessionMode(
                    id=mode.id,
                    name=mode.name,
                    description=mode.description,
                )
                for mode in self.modes
            ],
            current_mode_id=self._current_mode_id(session),
        )

    def get_session_metadata(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> dict[str, JsonValue]:
        del agent
        modes: list[JsonValue] = [
            {
                "description": mode.description,
                "id": mode.id,
                "name": mode.name,
                "plan_mode": mode.plan_mode,
                "plan_tools": mode.plan_tools,
            }
            for mode in self.modes
        ]
        metadata: dict[str, JsonValue] = {
            "current_mode_id": self._current_mode_id(session),
            "modes": modes,
        }
        if self.supports_plan_generation_selection():
            metadata["current_plan_generation_type"] = self.current_plan_generation_type(session)
            metadata["supported_plan_generation_types"] = list(_PLAN_GENERATION_CONFIG_OPTIONS)
        return metadata

    def get_config_options(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> list[ConfigOption]:
        del agent
        if not self.supports_plan_generation_selection():
            return []
        return [
            SessionConfigOptionSelect(
                id=self.plan_generation_config_id,
                name=self.plan_generation_config_name,
                category="agent",
                description=self.plan_generation_config_description,
                type="select",
                current_value=self.current_plan_generation_type(session),
                options=[
                    SessionConfigSelectOption(
                        value=value,
                        name="Tool-Based" if value == "tools" else "Structured",
                    )
                    for value in _PLAN_GENERATION_CONFIG_OPTIONS
                ],
            )
        ]

    def set_mode(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
        mode_id: str,
    ) -> ModeState | None:
        del agent
        if self._find_mode(mode_id) is None:
            return None
        session.config_values[self.mode_config_key] = mode_id
        return ModeState(
            modes=[
                SessionMode(
                    id=mode.id,
                    name=mode.name,
                    description=mode.description,
                )
                for mode in self.modes
            ],
            current_mode_id=mode_id,
        )

    def set_config_option(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
        config_id: str,
        value: str | bool,
    ) -> list[ConfigOption] | None:
        if (
            not self.supports_plan_generation_selection()
            or config_id != self.plan_generation_config_id
            or not isinstance(value, str)
            or value not in _PLAN_GENERATION_CONFIG_OPTIONS
        ):
            return None
        if value == self.default_plan_generation_type:
            session.config_values.pop(self.plan_generation_config_id, None)
        else:
            session.config_values[self.plan_generation_config_id] = value
        return self.get_config_options(session, agent)

    def _current_mode_id(self, session: AcpSessionContext) -> str:
        configured_mode = session.config_values.get(self.mode_config_key)
        if isinstance(configured_mode, str) and self._find_mode(configured_mode) is not None:
            return configured_mode
        return self.default_mode_id

    def _find_mode(self, mode_id: str) -> PrepareToolsMode[AgentDepsT] | None:
        for mode in self.modes:
            if mode.id == mode_id:
                return mode
        return None

    def _require_mode(self, mode_id: str) -> PrepareToolsMode[AgentDepsT]:
        mode = self._find_mode(mode_id)
        if mode is None:
            raise ValueError(f"Unknown prepare-tools mode: {mode_id!r}")
        return mode

    def current_mode(self, session: AcpSessionContext) -> PrepareToolsMode[AgentDepsT]:
        return self._require_mode(self._current_mode_id(session))

    def supports_plan_generation_selection(self) -> bool:
        return any(mode.plan_mode for mode in self.modes)

    def current_plan_generation_type(self, session: AcpSessionContext) -> PlanGenerationType:
        configured_value = session.config_values.get(self.plan_generation_config_id)
        if (
            isinstance(configured_value, str)
            and configured_value in _PLAN_GENERATION_CONFIG_OPTIONS
        ):
            return cast(PlanGenerationType, configured_value)
        return self.default_plan_generation_type

    def uses_tool_plan_generation(self, session: AcpSessionContext) -> bool:
        return self.current_plan_generation_type(session) == "tools"

    def uses_structured_plan_generation(self, session: AcpSessionContext) -> bool:
        return self.current_plan_generation_type(session) == "structured"

    def is_plan_mode(self, session: AcpSessionContext) -> bool:
        return self.current_mode(session).plan_mode

    def supports_plan_tools(self, session: AcpSessionContext) -> bool:
        mode = self.current_mode(session)
        return mode.plan_mode or mode.plan_tools

    def supports_plan_write_tools(self, session: AcpSessionContext) -> bool:
        mode = self.current_mode(session)
        return mode.plan_tools or (mode.plan_mode and self.uses_tool_plan_generation(session))

    def supports_plan_progress(self, session: AcpSessionContext) -> bool:
        return self.current_mode(session).plan_tools
