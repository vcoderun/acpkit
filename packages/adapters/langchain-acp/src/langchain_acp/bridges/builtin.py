from __future__ import annotations as _annotations

from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from typing import cast

from acp.schema import (
    ModelInfo,
    PlanEntry,
    PlanEntryPriority,
    PlanEntryStatus,
    SessionMode,
    ToolKind,
)

from ..providers import (
    ConfigOption,
    ConfigOptionsProvider,
    ModelSelectionState,
    ModeState,
    SessionModelsProvider,
    SessionModesProvider,
)
from ..session.state import AcpSessionContext, JsonValue
from .base import CapabilityBridge

__all__ = (
    "ConfigOptionsBridge",
    "DeepAgentsCompatibilityBridge",
    "ModeSelectionBridge",
    "ModelSelectionBridge",
    "ToolSurfaceBridge",
)


@dataclass(slots=True, frozen=True, kw_only=True)
class ToolSurfaceBridge(CapabilityBridge):
    tool_kinds: Mapping[str, ToolKind] = field(default_factory=dict)
    approval_policy_keys: Mapping[str, str] = field(default_factory=dict)

    def get_tool_kind(self, tool_name: str, raw_input: JsonValue | None = None) -> ToolKind | None:
        del raw_input
        return self.tool_kinds.get(tool_name)

    def get_approval_policy_key(
        self,
        tool_name: str,
        raw_input: JsonValue | None = None,
    ) -> str | None:
        del raw_input
        return self.approval_policy_keys.get(tool_name)


@dataclass(slots=True, frozen=True, kw_only=True)
class ModelSelectionBridge(CapabilityBridge):
    available_models: tuple[ModelInfo, ...] = ()
    default_model_id: str | None = None
    provider: SessionModelsProvider | None = None

    def get_model_state(
        self,
        session: AcpSessionContext,
    ) -> ModelSelectionState | None | Awaitable[ModelSelectionState | None]:
        if self.provider is not None:
            return self.provider.get_model_state(session=session)
        if not self.available_models:
            return None
        current_model_id = session.session_model_id or self._default_model_id()
        return ModelSelectionState(
            available_models=list(self.available_models),
            current_model_id=current_model_id,
        )

    def set_model(
        self,
        session: AcpSessionContext,
        model_id: str,
    ) -> ModelSelectionState | None | Awaitable[ModelSelectionState | None]:
        if self.provider is not None:
            return self.provider.set_model(session=session, model_id=model_id)
        if not any(model.model_id == model_id for model in self.available_models):
            return None
        session.session_model_id = model_id
        return self.get_model_state(session)

    def _default_model_id(self) -> str | None:
        if self.default_model_id is not None:
            return self.default_model_id
        if self.available_models:
            return self.available_models[0].model_id
        return None


@dataclass(slots=True, frozen=True, kw_only=True)
class ModeSelectionBridge(CapabilityBridge):
    available_modes: tuple[SessionMode, ...] = ()
    default_mode_id: str | None = None
    provider: SessionModesProvider | None = None

    def get_mode_state(
        self,
        session: AcpSessionContext,
    ) -> ModeState | None | Awaitable[ModeState | None]:
        if self.provider is not None:
            return self.provider.get_mode_state(session=session)
        if not self.available_modes:
            return None
        current_mode_id = session.session_mode_id or self._default_mode_id()
        return ModeState(
            modes=list(self.available_modes),
            current_mode_id=current_mode_id,
        )

    def set_mode(
        self,
        session: AcpSessionContext,
        mode_id: str,
    ) -> ModeState | None | Awaitable[ModeState | None]:
        if self.provider is not None:
            return self.provider.set_mode(session=session, mode_id=mode_id)
        if not any(mode.id == mode_id for mode in self.available_modes):
            return None
        session.session_mode_id = mode_id
        return self.get_mode_state(session)

    def _default_mode_id(self) -> str | None:
        if self.default_mode_id is not None:
            return self.default_mode_id
        if self.available_modes:
            return self.available_modes[0].id
        return None


@dataclass(slots=True, frozen=True, kw_only=True)
class ConfigOptionsBridge(CapabilityBridge):
    provider: ConfigOptionsProvider

    def get_config_options(
        self,
        session: AcpSessionContext,
    ) -> list[ConfigOption] | None | Awaitable[list[ConfigOption] | None]:
        return self.provider.get_config_options(session=session)

    def set_config_option(
        self,
        session: AcpSessionContext,
        config_id: str,
        value: str | bool,
    ) -> list[ConfigOption] | None | Awaitable[list[ConfigOption] | None]:
        return self.provider.set_config_option(session=session, config_id=config_id, value=value)


@dataclass(slots=True, frozen=True, kw_only=True)
class DeepAgentsCompatibilityBridge(CapabilityBridge):
    metadata_key: str | None = "deepagents"

    def get_session_metadata(self, session: AcpSessionContext) -> dict[str, JsonValue] | None:
        plan_generation_type = session.config_values.get("plan_generation_type")
        metadata: dict[str, JsonValue] = {
            "cwd": str(session.cwd),
            "mode": session.session_mode_id,
            "model": session.session_model_id,
        }
        if isinstance(plan_generation_type, str):
            metadata["plan_generation_type"] = plan_generation_type
        return metadata

    def extract_plan_entries(self, payload: JsonValue) -> list[PlanEntry] | None:
        if not isinstance(payload, dict):
            return None
        todos = payload.get("todos")
        if not isinstance(todos, list):
            return None
        entries: list[PlanEntry] = []
        for todo in todos:
            if not isinstance(todo, dict):
                continue
            content = todo.get("content")
            if not isinstance(content, str):
                continue
            status = todo.get("status", "pending")
            if status not in {"pending", "in_progress", "completed"}:
                status = "pending"
            priority = todo.get("priority", "medium")
            if priority not in {"high", "medium", "low"}:
                priority = "medium"
            entries.append(
                PlanEntry(
                    content=content,
                    status=cast(PlanEntryStatus, status),
                    priority=cast(PlanEntryPriority, priority),
                )
            )
        return entries
