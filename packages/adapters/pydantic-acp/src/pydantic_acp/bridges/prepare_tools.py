from __future__ import annotations as _annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from acp.schema import SessionMode
from pydantic_ai.capabilities import PrepareTools
from pydantic_ai.tools import RunContext, ToolDefinition, ToolsPrepareFunc

from .._slash_commands import validate_mode_command_ids
from ..agent_types import RuntimeAgent
from ..awaitables import resolve_value
from ..providers import ModeState
from ..session.state import AcpSessionContext, JsonValue
from .base import BufferedCapabilityBridge

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)

__all__ = (
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

    def __post_init__(self) -> None:
        if not self.modes:
            raise ValueError("PrepareToolsBridge requires at least one mode.")
        validate_mode_command_ids(mode.id for mode in self.modes)
        mode_ids = {mode.id for mode in self.modes}
        if self.default_mode_id not in mode_ids:
            raise ValueError("PrepareToolsBridge default mode must match one of the modes.")
        if sum(1 for mode in self.modes if mode.plan_mode) > 1:
            raise ValueError("PrepareToolsBridge supports at most one `plan_mode=True` mode.")

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
                "plan_tools": mode.plan_mode or mode.plan_tools,
            }
            for mode in self.modes
        ]
        return {
            "current_mode_id": self._current_mode_id(session),
            "modes": modes,
        }

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

    def is_plan_mode(self, session: AcpSessionContext) -> bool:
        return self.current_mode(session).plan_mode

    def supports_plan_tools(self, session: AcpSessionContext) -> bool:
        mode = self.current_mode(session)
        return mode.plan_mode or mode.plan_tools
