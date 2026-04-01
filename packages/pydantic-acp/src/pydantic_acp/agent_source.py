from __future__ import annotations as _annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from pydantic_ai import Agent as PydanticAgent

from .awaitables import is_awaitable, is_resolved
from .session.state import AcpSessionContext

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)
OutputDataT = TypeVar("OutputDataT", covariant=True)

__all__ = ("AgentFactory", "AgentSource", "FactoryAgentSource", "StaticAgentSource")


class AgentFactory(Protocol[AgentDepsT, OutputDataT]):
    def __call__(
        self, session: AcpSessionContext
    ) -> (
        PydanticAgent[AgentDepsT, OutputDataT] | Awaitable[PydanticAgent[AgentDepsT, OutputDataT]]
    ): ...


class AgentSource(Protocol[AgentDepsT, OutputDataT]):
    async def get_agent(
        self, session: AcpSessionContext
    ) -> PydanticAgent[AgentDepsT, OutputDataT]: ...


@dataclass(slots=True)
class StaticAgentSource(Generic[AgentDepsT, OutputDataT]):
    agent: PydanticAgent[AgentDepsT, OutputDataT]

    async def get_agent(self, session: AcpSessionContext) -> PydanticAgent[AgentDepsT, OutputDataT]:
        del session
        return self.agent


@dataclass(slots=True)
class FactoryAgentSource(Generic[AgentDepsT, OutputDataT]):
    factory: AgentFactory[AgentDepsT, OutputDataT]

    async def get_agent(self, session: AcpSessionContext) -> PydanticAgent[AgentDepsT, OutputDataT]:
        candidate = self.factory(session)
        if is_awaitable(candidate):
            return await candidate
        assert is_resolved(candidate)
        return candidate
