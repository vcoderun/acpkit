from __future__ import annotations as _annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any, Protocol, cast

from langgraph.graph.state import CompiledStateGraph

from .session.state import AcpSessionContext

__all__ = (
    "CompiledAgentGraph",
    "FactoryGraphSource",
    "GraphFactory",
    "GraphSource",
    "StaticGraphSource",
)

CompiledAgentGraph = CompiledStateGraph[Any, Any, Any, Any]


class GraphFactory(Protocol):
    def __call__(
        self, session: AcpSessionContext
    ) -> CompiledAgentGraph | Awaitable[CompiledAgentGraph]: ...


class GraphSource(Protocol):
    async def get_graph(self, session: AcpSessionContext) -> CompiledAgentGraph: ...


@dataclass(slots=True)
class StaticGraphSource:
    graph: CompiledAgentGraph

    async def get_graph(self, session: AcpSessionContext) -> CompiledAgentGraph:
        del session
        return self.graph


@dataclass(slots=True)
class FactoryGraphSource:
    factory: GraphFactory

    async def get_graph(self, session: AcpSessionContext) -> CompiledAgentGraph:
        candidate = self.factory(session)
        if isawaitable(candidate):
            return cast(CompiledAgentGraph, await candidate)
        return cast(CompiledAgentGraph, candidate)
