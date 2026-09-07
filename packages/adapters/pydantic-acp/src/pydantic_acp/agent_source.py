from __future__ import annotations as _annotations

from collections.abc import AsyncIterator, Awaitable, Sequence
from contextlib import (
    AbstractAsyncContextManager,
    AbstractContextManager,
    AsyncExitStack,
    asynccontextmanager,
)
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeAlias, TypeVar, cast, runtime_checkable

from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.messages import ModelMessage

from .awaitables import resolve_value
from .session.state import AcpSessionContext

AgentFactoryDepsT = TypeVar("AgentFactoryDepsT", contravariant=True)
AgentFactoryOutputDataT = TypeVar("AgentFactoryOutputDataT", covariant=True)
AgentDepsT = TypeVar("AgentDepsT")
OutputDataT = TypeVar("OutputDataT")
AgentHookDepsT = TypeVar("AgentHookDepsT", covariant=True)
AgentHookOutputDataT = TypeVar("AgentHookOutputDataT", contravariant=True)

AgentPromptRunScope: TypeAlias = AbstractContextManager[None] | AbstractAsyncContextManager[None]

__all__ = (
    "AgentFactory",
    "AgentHistoryProvider",
    "AgentPromptRunScope",
    "AgentPromptScopeProvider",
    "AgentSessionLifecycle",
    "AgentSource",
    "FactoryAgentSource",
    "StaticAgentSource",
)


class AgentFactory(Protocol[AgentFactoryDepsT, AgentFactoryOutputDataT]):
    def __call__(
        self,
        session: AcpSessionContext,
    ) -> (
        PydanticAgent[AgentFactoryDepsT, AgentFactoryOutputDataT]
        | Awaitable[PydanticAgent[AgentFactoryDepsT, AgentFactoryOutputDataT]]
    ): ...


class AgentSource(Protocol[AgentDepsT, OutputDataT]):
    async def get_agent(
        self,
        session: AcpSessionContext,
    ) -> PydanticAgent[AgentDepsT, OutputDataT]: ...

    async def get_deps(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> AgentDepsT | None: ...


@runtime_checkable
class AgentPromptScopeProvider(Protocol[AgentHookDepsT, AgentHookOutputDataT]):
    def prompt_run_scope(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentHookDepsT, AgentHookOutputDataT],
    ) -> AgentPromptRunScope:
        """Bind source-owned state for one complete prompt execution."""
        ...


@runtime_checkable
class AgentHistoryProvider(Protocol[AgentHookDepsT, AgentHookOutputDataT]):
    def get_message_history(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentHookDepsT, AgentHookOutputDataT],
        persisted_history: Sequence[ModelMessage],
    ) -> Sequence[ModelMessage] | None | Awaitable[Sequence[ModelMessage] | None]:
        """Return prompt history, or ``None`` when the agent owns history replay."""
        ...


@runtime_checkable
class AgentSessionLifecycle(Protocol):
    def close_session(self, session: AcpSessionContext) -> None | Awaitable[None]:
        """Release source-owned resources for one persisted ACP session."""
        ...


@dataclass(slots=True)
class StaticAgentSource(Generic[AgentDepsT, OutputDataT]):
    agent: PydanticAgent[AgentDepsT, OutputDataT]
    deps: AgentDepsT | None = None

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
        return await resolve_value(self.factory(session))

    async def get_deps(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> AgentDepsT | None:
        del session, agent
        return None


@asynccontextmanager
async def _agent_prompt_run_scope(
    source: AgentSource[AgentDepsT, OutputDataT],
    session: AcpSessionContext,
    agent: PydanticAgent[AgentDepsT, OutputDataT],
) -> AsyncIterator[None]:
    async with AsyncExitStack() as stack:
        if isinstance(source, AgentPromptScopeProvider):
            scope_provider = cast(
                "AgentPromptScopeProvider[AgentDepsT, OutputDataT]",
                source,
            )
            scope = scope_provider.prompt_run_scope(session, agent)
            if isinstance(scope, AbstractAsyncContextManager):
                await stack.enter_async_context(scope)
            elif isinstance(scope, AbstractContextManager):
                stack.enter_context(scope)
            else:
                raise TypeError(
                    "Agent prompt scope providers must return a sync or async context manager."
                )
        yield


async def _agent_message_history(
    source: AgentSource[AgentDepsT, OutputDataT],
    session: AcpSessionContext,
    agent: PydanticAgent[AgentDepsT, OutputDataT],
    persisted_history: list[ModelMessage],
) -> list[ModelMessage] | None:
    if not isinstance(source, AgentHistoryProvider):
        return persisted_history
    history_provider = cast("AgentHistoryProvider[AgentDepsT, OutputDataT]", source)
    history = await resolve_value(
        history_provider.get_message_history(session, agent, tuple(persisted_history))
    )
    return None if history is None else list(history)


async def _close_agent_source_session(
    source: AgentSource[Any, Any],
    session: AcpSessionContext,
) -> None:
    if isinstance(source, AgentSessionLifecycle):
        await resolve_value(source.close_session(session))
