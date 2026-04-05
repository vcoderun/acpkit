from __future__ import annotations as _annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from typing import TypeVar

from acp import run_agent
from acp.interfaces import Agent as AcpAgent
from pydantic_ai import Agent as PydanticAgent

from ..agent_source import (
    AgentFactory,
    AgentSource,
    FactoryAgentSource,
    StaticAgentSource,
)
from ..config import DEFAULT_AGENT_NAME, AdapterConfig
from ..projection import ProjectionMap
from .adapter import PydanticAcpAgent

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)
OutputDataT = TypeVar("OutputDataT", covariant=True)

__all__ = ("create_acp_agent", "run_acp")


def create_acp_agent(
    agent: PydanticAgent[AgentDepsT, OutputDataT] | None = None,
    *,
    agent_factory: AgentFactory[AgentDepsT, OutputDataT] | None = None,
    agent_source: AgentSource[AgentDepsT, OutputDataT] | None = None,
    config: AdapterConfig | None = None,
    projection_maps: Sequence[ProjectionMap] | None = None,
) -> AcpAgent:
    resolved_source = _resolve_agent_source(
        agent=agent,
        agent_factory=agent_factory,
        agent_source=agent_source,
    )
    resolved_config = _resolve_config(
        config=config,
        agent_name=agent.name if agent is not None else None,
        projection_maps=projection_maps,
    )
    adapter = PydanticAcpAgent(resolved_source, config=resolved_config)
    return adapter


def run_acp(
    agent: PydanticAgent[AgentDepsT, OutputDataT] | None = None,
    *,
    agent_factory: AgentFactory[AgentDepsT, OutputDataT] | None = None,
    agent_source: AgentSource[AgentDepsT, OutputDataT] | None = None,
    config: AdapterConfig | None = None,
    projection_maps: Sequence[ProjectionMap] | None = None,
) -> None:
    adapter = create_acp_agent(
        agent=agent,
        agent_factory=agent_factory,
        agent_source=agent_source,
        config=config,
        projection_maps=projection_maps,
    )
    asyncio.run(run_agent(adapter))


def _resolve_agent_source(
    *,
    agent: PydanticAgent[AgentDepsT, OutputDataT] | None,
    agent_factory: AgentFactory[AgentDepsT, OutputDataT] | None,
    agent_source: AgentSource[AgentDepsT, OutputDataT] | None,
) -> AgentSource[AgentDepsT, OutputDataT]:
    provided_count = sum(provided is not None for provided in (agent, agent_factory, agent_source))
    if provided_count != 1:
        raise ValueError(
            "Exactly one of `agent`, `agent_factory`, or `agent_source` must be provided."
        )
    if agent is not None:
        return StaticAgentSource(agent)
    if agent_factory is not None:
        return FactoryAgentSource(agent_factory)
    assert agent_source is not None
    return agent_source


def _resolve_config(
    *,
    config: AdapterConfig | None,
    agent_name: str | None,
    projection_maps: Sequence[ProjectionMap] | None,
) -> AdapterConfig:
    resolved_config = config or AdapterConfig()
    if projection_maps is not None:
        resolved_config = replace(resolved_config, projection_maps=tuple(projection_maps))
    if agent_name is None:
        return resolved_config
    if resolved_config.agent_name != DEFAULT_AGENT_NAME:
        return resolved_config
    return replace(resolved_config, agent_name=agent_name)
