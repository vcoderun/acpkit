from __future__ import annotations as _annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic_ai._history_processor import HistoryProcessor
from pydantic_ai._history_processor import (
    _HistoryProcessorAsyncWithCtx as HistoryProcessorWithContextAsync,
)
from pydantic_ai._history_processor import (
    _HistoryProcessorSyncWithCtx as HistoryProcessorWithContextSync,
)
from pydantic_ai.capabilities import AbstractCapability

from ..bridges import (
    CapabilityBridge,
    HistoryProcessorBridge,
    HistoryProcessorPlain,
    HookBridge,
    PrepareToolsBridge,
)
from ..session.state import AcpSessionContext

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)

__all__ = (
    "AgentBridgeBuilder",
    "AgentBridgeContributions",
)


@dataclass(slots=True, frozen=True, kw_only=True)
class AgentBridgeContributions(Generic[AgentDepsT]):
    capabilities: tuple[AbstractCapability[Any], ...]
    history_processors: tuple[HistoryProcessor[AgentDepsT], ...]


@dataclass(slots=True, frozen=True, kw_only=True)
class AgentBridgeBuilder(Generic[AgentDepsT]):
    session: AcpSessionContext
    capability_bridges: Sequence[CapabilityBridge]

    def build(
        self,
        *,
        capabilities: Sequence[AbstractCapability[AgentDepsT]] = (),
        contextual_history_processors: Sequence[
            HistoryProcessorWithContextSync[AgentDepsT]
            | HistoryProcessorWithContextAsync[AgentDepsT]
        ] = (),
        plain_history_processors: Sequence[HistoryProcessorPlain] = (),
    ) -> AgentBridgeContributions[AgentDepsT]:
        return AgentBridgeContributions(
            capabilities=self.build_capabilities(capabilities=capabilities),
            history_processors=self.build_history_processors(
                contextual_history_processors=contextual_history_processors,
                plain_history_processors=plain_history_processors,
            ),
        )

    def build_capabilities(
        self,
        *,
        capabilities: Sequence[AbstractCapability[AgentDepsT]] = (),
    ) -> tuple[AbstractCapability[Any], ...]:
        resolved_capabilities: list[AbstractCapability[Any]] = list(capabilities)
        for bridge in self.capability_bridges:
            resolved_capabilities.extend(_build_bridge_capabilities(bridge, self.session))
        return tuple(resolved_capabilities)

    def build_history_processors(
        self,
        *,
        contextual_history_processors: Sequence[
            HistoryProcessorWithContextSync[AgentDepsT]
            | HistoryProcessorWithContextAsync[AgentDepsT]
        ] = (),
        plain_history_processors: Sequence[HistoryProcessorPlain] = (),
    ) -> tuple[HistoryProcessor[AgentDepsT], ...]:
        history_bridges = [
            bridge
            for bridge in self.capability_bridges
            if isinstance(bridge, HistoryProcessorBridge)
        ]
        resolved_history_processors: list[HistoryProcessor[AgentDepsT]] = []

        for processor in plain_history_processors:
            wrapped_processor = processor
            for bridge in history_bridges:
                wrapped_processor = bridge.wrap_plain_processor(
                    self.session,
                    wrapped_processor,
                    name=_processor_name(processor),
                )
            resolved_history_processors.append(wrapped_processor)

        for processor in contextual_history_processors:
            wrapped_processor = processor
            for bridge in history_bridges:
                wrapped_processor = bridge.wrap_contextual_processor(
                    self.session,
                    wrapped_processor,
                    name=_processor_name(processor),
                )
            resolved_history_processors.append(wrapped_processor)

        return tuple(resolved_history_processors)


def _build_bridge_capabilities(
    bridge: CapabilityBridge,
    session: AcpSessionContext,
) -> tuple[AbstractCapability[Any], ...]:
    if isinstance(bridge, HookBridge):
        return (bridge.build_capability(session),)
    if isinstance(bridge, PrepareToolsBridge):
        return (bridge.build_capability(session),)
    return ()


def _processor_name(processor: object) -> str:
    name = getattr(processor, "__name__", "")
    return name or "history_processor"
