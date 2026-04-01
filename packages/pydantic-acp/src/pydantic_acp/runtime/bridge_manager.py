from __future__ import annotations as _annotations

from collections.abc import Sequence
from dataclasses import dataclass

from acp.schema import McpCapabilities, ToolKind

from ..agent_types import RuntimeAgent
from ..bridges import CapabilityBridge
from ..projection import ToolClassifier
from ..providers import ConfigOption, ModeState
from ..session.state import AcpSessionContext, JsonValue, SessionTranscriptUpdate

__all__ = ("BridgeManager",)


@dataclass(slots=True, kw_only=True)
class BridgeManager:
    base_classifier: ToolClassifier
    bridges: Sequence[CapabilityBridge]

    @property
    def tool_classifier(self) -> ToolClassifier:
        return _BridgeAwareToolClassifier(
            base_classifier=self.base_classifier,
            bridges=self.bridges,
        )

    def drain_updates(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> list[SessionTranscriptUpdate]:
        updates: list[SessionTranscriptUpdate] = []
        for bridge in self.bridges:
            bridge_updates = bridge.drain_updates(session, agent)
            if bridge_updates:
                updates.extend(bridge_updates)
        return updates

    def get_config_options(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> list[ConfigOption] | None:
        options: list[ConfigOption] = []
        for bridge in self.bridges:
            bridge_options = bridge.get_config_options(session, agent)
            if bridge_options:
                options.extend(bridge_options)
        return options or None

    def get_mcp_capabilities(self, agent: RuntimeAgent | None = None) -> McpCapabilities:
        merged = McpCapabilities()
        for bridge in self.bridges:
            capabilities = bridge.get_mcp_capabilities(agent)
            if capabilities is None:
                continue
            merged.http = bool(merged.http or capabilities.http)
            merged.sse = bool(merged.sse or capabilities.sse)
        return merged

    def get_mode_state(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> ModeState | None:
        for bridge in self.bridges:
            mode_state = bridge.get_mode_state(session, agent)
            if mode_state is not None:
                return mode_state
        return None

    def get_metadata_sections(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> dict[str, JsonValue]:
        metadata: dict[str, JsonValue] = {}
        for bridge in self.bridges:
            if bridge.metadata_key is None:
                continue
            bridge_metadata = bridge.get_session_metadata(session, agent)
            if bridge_metadata is not None:
                metadata[bridge.metadata_key] = bridge_metadata
        return metadata

    def set_config_option(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
        config_id: str,
        value: str | bool,
    ) -> list[ConfigOption] | None:
        for bridge in self.bridges:
            options = bridge.set_config_option(session, agent, config_id, value)
            if options is not None:
                return options
        return None

    def set_mode(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
        mode_id: str,
    ) -> ModeState | None:
        for bridge in self.bridges:
            mode_state = bridge.set_mode(session, agent, mode_id)
            if mode_state is not None:
                return mode_state
        return None


@dataclass(slots=True, frozen=True, kw_only=True)
class _BridgeAwareToolClassifier:
    base_classifier: ToolClassifier
    bridges: Sequence[CapabilityBridge]

    def classify(self, tool_name: str, raw_input: object | None = None) -> ToolKind:
        for bridge in self.bridges:
            bridge_kind = bridge.get_tool_kind(tool_name, raw_input)
            if bridge_kind is not None:
                return bridge_kind
        return self.base_classifier.classify(tool_name, raw_input)

    def approval_policy_key(self, tool_name: str, raw_input: object | None = None) -> str:
        for bridge in self.bridges:
            approval_policy_key = bridge.get_approval_policy_key(tool_name, raw_input)
            if approval_policy_key is not None:
                return approval_policy_key
        return self.base_classifier.approval_policy_key(tool_name, raw_input)
