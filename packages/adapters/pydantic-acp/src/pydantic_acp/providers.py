from __future__ import annotations as _annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Protocol, TypeAlias

from acp.schema import (
    PlanEntry,
    SessionConfigOptionBoolean,
    SessionConfigOptionSelect,
    SessionMode,
)

from .agent_types import RuntimeAgent
from .models import AdapterModel
from .session.state import AcpSessionContext, JsonValue

ConfigOption: TypeAlias = SessionConfigOptionSelect | SessionConfigOptionBoolean

__all__ = (
    "ApprovalStateProvider",
    "ConfigOption",
    "ConfigOptionsProvider",
    "ModeState",
    "ModelSelectionState",
    "PlanProvider",
    "SessionModelsProvider",
    "SessionModesProvider",
)


@dataclass(slots=True, frozen=True, kw_only=True)
class ModelSelectionState:
    available_models: list[AdapterModel]
    current_model_id: str | None
    allow_any_model_id: bool = False
    enable_config_option: bool = True
    config_option_name: str = "Model"
    config_option_description: str | None = "Session-local model override."


@dataclass(slots=True, frozen=True, kw_only=True)
class ModeState:
    modes: list[SessionMode]
    current_mode_id: str | None = None


class SessionModelsProvider(Protocol):
    def get_model_state(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> ModelSelectionState | None | Awaitable[ModelSelectionState | None]: ...

    def set_model(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
        model_id: str,
    ) -> ModelSelectionState | None | Awaitable[ModelSelectionState | None]: ...


class SessionModesProvider(Protocol):
    def get_mode_state(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> ModeState | None | Awaitable[ModeState | None]: ...

    def set_mode(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
        mode_id: str,
    ) -> ModeState | None | Awaitable[ModeState | None]: ...


class ConfigOptionsProvider(Protocol):
    def get_config_options(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> list[ConfigOption] | None | Awaitable[list[ConfigOption] | None]: ...

    def set_config_option(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
        config_id: str,
        value: str | bool,
    ) -> list[ConfigOption] | None | Awaitable[list[ConfigOption] | None]: ...


class PlanProvider(Protocol):
    def get_plan(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> list[PlanEntry] | None | Awaitable[list[PlanEntry] | None]: ...


class ApprovalStateProvider(Protocol):
    def get_approval_state(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> dict[str, JsonValue] | None | Awaitable[dict[str, JsonValue] | None]: ...
