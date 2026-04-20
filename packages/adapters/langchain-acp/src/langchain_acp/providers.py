from __future__ import annotations as _annotations

from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeAlias

from acp.schema import (
    ModelInfo,
    PlanEntry,
    SessionConfigOptionBoolean,
    SessionConfigOptionSelect,
    SessionMode,
)

from .session.state import AcpSessionContext

ConfigOption: TypeAlias = SessionConfigOptionSelect | SessionConfigOptionBoolean

__all__ = (
    "ConfigOption",
    "ConfigOptionsProvider",
    "ModeState",
    "ModelSelectionState",
    "NativePlanPersistenceProvider",
    "PlanProvider",
    "SessionModelsProvider",
    "SessionModesProvider",
)


@dataclass(slots=True, frozen=True, kw_only=True)
class ModelSelectionState:
    available_models: list[ModelInfo]
    current_model_id: str | None
    allow_any_model_id: bool = False
    enable_config_option: bool = True
    config_option_name: str = "Model"


@dataclass(slots=True, frozen=True, kw_only=True)
class ModeState:
    modes: list[SessionMode]
    current_mode_id: str | None = None
    enable_config_option: bool = True
    config_option_name: str = "Mode"


class SessionModelsProvider(Protocol):
    def get_model_state(
        self,
        session: AcpSessionContext,
    ) -> ModelSelectionState | None | Awaitable[ModelSelectionState | None]: ...

    def set_model(
        self,
        session: AcpSessionContext,
        model_id: str,
    ) -> ModelSelectionState | None | Awaitable[ModelSelectionState | None]: ...


class SessionModesProvider(Protocol):
    def get_mode_state(
        self,
        session: AcpSessionContext,
    ) -> ModeState | None | Awaitable[ModeState | None]: ...

    def set_mode(
        self,
        session: AcpSessionContext,
        mode_id: str,
    ) -> ModeState | None | Awaitable[ModeState | None]: ...


class ConfigOptionsProvider(Protocol):
    def get_config_options(
        self,
        session: AcpSessionContext,
    ) -> list[ConfigOption] | None | Awaitable[list[ConfigOption] | None]: ...

    def set_config_option(
        self,
        session: AcpSessionContext,
        config_id: str,
        value: str | bool,
    ) -> list[ConfigOption] | None | Awaitable[list[ConfigOption] | None]: ...


class PlanProvider(Protocol):
    def get_plan(
        self,
        session: AcpSessionContext,
    ) -> list[PlanEntry] | None | Awaitable[list[PlanEntry] | None]: ...


class NativePlanPersistenceProvider(Protocol):
    def persist_plan_state(
        self,
        session: AcpSessionContext,
        *,
        entries: Sequence[PlanEntry],
        plan_markdown: str | None,
    ) -> None | Awaitable[None]: ...
