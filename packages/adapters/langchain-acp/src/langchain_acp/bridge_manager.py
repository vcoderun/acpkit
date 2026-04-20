from __future__ import annotations as _annotations

from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from inspect import isawaitable
from typing import TypeVar, cast

from acp.schema import PlanEntry, ToolKind

from .bridges import CapabilityBridge
from .projection import ToolClassifier
from .providers import ConfigOption, ModelSelectionState, ModeState
from .session.state import AcpSessionContext, JsonValue, SessionTranscriptUpdate

__all__ = ("BridgeManager",)

_T = TypeVar("_T")


async def _resolve_bridge_value(value: _T | Awaitable[_T]) -> _T:
    if isawaitable(value):
        return await cast(Awaitable[_T], value)
    return value


@dataclass(slots=True, kw_only=True)
class BridgeManager:
    base_classifier: ToolClassifier
    bridges: Sequence[CapabilityBridge]

    @property
    def metadata_keys(self) -> tuple[str, ...]:
        return tuple(
            bridge.metadata_key for bridge in self.bridges if bridge.metadata_key is not None
        )

    @property
    def tool_classifier(self) -> ToolClassifier:
        return _BridgeAwareToolClassifier(
            base_classifier=self.base_classifier,
            bridges=self.bridges,
        )

    def drain_updates(self, session: AcpSessionContext) -> list[SessionTranscriptUpdate]:
        updates: list[SessionTranscriptUpdate] = []
        for bridge in self.bridges:
            bridge_updates = bridge.drain_updates(session)
            if bridge_updates:
                updates.extend(bridge_updates)
        return updates

    async def get_config_options(
        self,
        session: AcpSessionContext,
    ) -> list[ConfigOption] | None:
        options: list[ConfigOption] = []
        for bridge in self.bridges:
            bridge_options = await _resolve_bridge_value(bridge.get_config_options(session))
            if bridge_options is not None:
                options.extend(bridge_options)
        return options or None

    async def get_mode_state(
        self,
        session: AcpSessionContext,
    ) -> ModeState | None:
        for bridge in self.bridges:
            mode_state = await _resolve_bridge_value(bridge.get_mode_state(session))
            if mode_state is not None:
                if mode_state.current_mode_id is not None:
                    session.session_mode_id = mode_state.current_mode_id
                return mode_state
        return None

    async def get_model_state(
        self,
        session: AcpSessionContext,
    ) -> ModelSelectionState | None:
        for bridge in self.bridges:
            model_state = await _resolve_bridge_value(bridge.get_model_state(session))
            if model_state is not None:
                if model_state.current_model_id is not None:
                    session.session_model_id = model_state.current_model_id
                return model_state
        return None

    def get_metadata_sections(self, session: AcpSessionContext) -> dict[str, JsonValue]:
        metadata: dict[str, JsonValue] = {}
        for bridge in self.bridges:
            if bridge.metadata_key is None:
                continue
            bridge_metadata = bridge.get_session_metadata(session)
            if bridge_metadata is not None:
                metadata[bridge.metadata_key] = bridge_metadata
        return metadata

    def extract_plan_entries(self, payload: JsonValue) -> list[PlanEntry] | None:
        for bridge in self.bridges:
            plan_entries = bridge.extract_plan_entries(payload)
            if plan_entries is not None:
                return plan_entries
        return None

    async def set_config_option(
        self,
        session: AcpSessionContext,
        config_id: str,
        value: str | bool,
    ) -> list[ConfigOption] | None:
        for bridge in self.bridges:
            options = await _resolve_bridge_value(
                bridge.set_config_option(session, config_id, value)
            )
            if options is not None:
                return options
        return None

    async def set_mode(
        self,
        session: AcpSessionContext,
        mode_id: str,
    ) -> ModeState | None:
        for bridge in self.bridges:
            mode_state = await _resolve_bridge_value(bridge.set_mode(session, mode_id))
            if mode_state is not None:
                if mode_state.current_mode_id is None:
                    return None
                session.session_mode_id = mode_state.current_mode_id
                return mode_state
        return None

    async def set_model(
        self,
        session: AcpSessionContext,
        model_id: str,
    ) -> ModelSelectionState | None:
        for bridge in self.bridges:
            model_state = await _resolve_bridge_value(bridge.set_model(session, model_id))
            if model_state is not None:
                if model_state.current_model_id is None:
                    return None
                session.session_model_id = model_state.current_model_id
                return model_state
        return None


@dataclass(slots=True, frozen=True, kw_only=True)
class _BridgeAwareToolClassifier:
    base_classifier: ToolClassifier
    bridges: Sequence[CapabilityBridge]

    def classify(self, tool_name: str, raw_input: JsonValue | None = None) -> ToolKind:
        for bridge in self.bridges:
            bridge_kind = bridge.get_tool_kind(tool_name, raw_input)
            if bridge_kind is not None:
                return bridge_kind
        return self.base_classifier.classify(tool_name, raw_input)

    def approval_policy_key(self, tool_name: str, raw_input: JsonValue | None = None) -> str:
        for bridge in self.bridges:
            approval_policy_key = bridge.get_approval_policy_key(tool_name, raw_input)
            if approval_policy_key is not None:
                return approval_policy_key
        return self.base_classifier.approval_policy_key(tool_name, raw_input)
