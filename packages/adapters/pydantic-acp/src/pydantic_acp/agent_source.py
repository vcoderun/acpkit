from __future__ import annotations as _annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from pydantic_ai import Agent as PydanticAgent

from .awaitables import is_awaitable, is_resolved
from .session.state import AcpSessionContext

AgentFactoryDepsT = TypeVar("AgentFactoryDepsT", contravariant=True)
AgentFactoryOutputDataT = TypeVar("AgentFactoryOutputDataT", covariant=True)
AgentDepsT = TypeVar("AgentDepsT")
OutputDataT = TypeVar("OutputDataT")

__all__ = ("AgentFactory", "AgentSource", "FactoryAgentSource", "StaticAgentSource")


class AgentFactory(Protocol[AgentFactoryDepsT, AgentFactoryOutputDataT]):
    def __call__(
        self, session: AcpSessionContext
    ) -> (
        PydanticAgent[AgentFactoryDepsT, AgentFactoryOutputDataT]
        | Awaitable[PydanticAgent[AgentFactoryDepsT, AgentFactoryOutputDataT]]
    ): ...


class AgentSource(Protocol[AgentDepsT, OutputDataT]):
    async def get_agent(
        self, session: AcpSessionContext
    ) -> PydanticAgent[AgentDepsT, OutputDataT]: ...

    async def get_deps(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> AgentDepsT | None: ...


@dataclass(slots=True)
class StaticAgentSource(Generic[AgentDepsT, OutputDataT]):
    agent: PydanticAgent[AgentDepsT, OutputDataT]
    deps: AgentDepsT | None

    async def get_agent(self, session: AcpSessionContext) -> PydanticAgent[AgentDepsT, OutputDataT]:
        del session
        return self.agent

    async def get_deps(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> AgentDepsT | None:
        del session, agent
        return self.deps


@dataclass(slots=True)
class FactoryAgentSource(Generic[AgentDepsT, OutputDataT]):
    factory: AgentFactory[AgentDepsT, OutputDataT]

    async def get_agent(self, session: AcpSessionContext) -> PydanticAgent[AgentDepsT, OutputDataT]:
        candidate = self.factory(session)
        if is_awaitable(candidate):
            return await candidate
        assert is_resolved(candidate)
        return candidate

    async def get_deps(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> AgentDepsT | None:
        del session, agent
        return None
