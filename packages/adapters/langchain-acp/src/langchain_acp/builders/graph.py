from __future__ import annotations as _annotations

from dataclasses import dataclass, field
from typing import Any

from ..bridge_manager import BridgeManager
from ..bridges import (
    CapabilityBridge,
    ConfigOptionsBridge,
    DeepAgentsCompatibilityBridge,
    ModelSelectionBridge,
    ModeSelectionBridge,
)
from ..config import AdapterConfig
from ..projection import ToolClassifier
from ..session.state import AcpSessionContext, JsonValue

__all__ = (
    "GraphBridgeBuilder",
    "GraphBuildContributions",
)


@dataclass(slots=True, kw_only=True)
class GraphBuildContributions:
    interrupt_configuration: dict[str, JsonValue] = field(default_factory=dict)
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    middleware: tuple[Any, ...] = ()
    response_format: Any = None
    system_prompt_parts: tuple[str, ...] = ()
    tools: tuple[Any, ...] = ()


@dataclass(slots=True, frozen=True, kw_only=True)
class GraphBridgeBuilder:
    base_classifier: ToolClassifier
    bridges: tuple[CapabilityBridge, ...]

    @classmethod
    def from_config(cls, config: AdapterConfig) -> GraphBridgeBuilder:
        builtins: list[CapabilityBridge] = [
            DeepAgentsCompatibilityBridge(),
            ModelSelectionBridge(
                available_models=tuple(config.available_models),
                default_model_id=config.default_model_id,
                provider=config.models_provider,
            ),
            ModeSelectionBridge(
                available_modes=tuple(config.available_modes),
                default_mode_id=config.default_mode_id,
                provider=config.modes_provider,
            ),
        ]
        if config.config_options_provider is not None:
            builtins.append(ConfigOptionsBridge(provider=config.config_options_provider))
        return cls(
            base_classifier=config.tool_classifier,
            bridges=(*config.capability_bridges, *builtins),
        )

    def build_manager(self) -> BridgeManager:
        return BridgeManager(base_classifier=self.base_classifier, bridges=self.bridges)

    def build_graph_contributions(self, session: AcpSessionContext) -> GraphBuildContributions:
        manager = self.build_manager()
        middleware: list[Any] = []
        tools: list[Any] = []
        system_prompt_parts: list[str] = []
        interrupt_configuration: dict[str, JsonValue] = {}
        response_format: Any = None
        for bridge in self.bridges:
            middleware.extend(bridge.get_middleware(session))
            tools.extend(bridge.get_tools(session))
            system_prompt_parts.extend(bridge.get_system_prompt_parts(session))
            bridge_interrupt_configuration = bridge.get_interrupt_configuration(session)
            if bridge_interrupt_configuration is not None:
                interrupt_configuration.update(bridge_interrupt_configuration)
            bridge_response_format = bridge.get_response_format(session)
            if bridge_response_format is not None:
                response_format = bridge_response_format
        return GraphBuildContributions(
            interrupt_configuration=interrupt_configuration,
            metadata=manager.get_metadata_sections(session),
            middleware=tuple(middleware),
            response_format=response_format,
            system_prompt_parts=tuple(system_prompt_parts),
            tools=tuple(tools),
        )
