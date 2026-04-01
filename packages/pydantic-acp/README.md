# pydantic-acp

`pydantic-acp` adapts `pydantic_ai.Agent` instances to the ACP agent interface.

## Public Entry Points

- `run_acp(...)`
- `create_acp_agent(...)`
- `AdapterConfig`
- `AcpSessionContext`
- `AgentSource`, `AgentFactory`, `StaticAgentSource`, `FactoryAgentSource`
- `AgentBridgeBuilder`
- provider protocols
- capability bridges
- host backends

## Implemented Scope

| Milestone | Status | Scope |
| --- | --- | --- |
| 1 | complete | Bare ACP adapter, session lifecycle, transcript replay, output serialization |
| 2 | complete | Session-local model selection |
| 3 | complete | Native deferred approval bridge |
| 4 | complete | Static agents, factories, and custom agent sources |
| 5 | complete | Provider interfaces for models, modes, config options, plans, and approval state |
| 6 | complete | Capability bridges and builder integration |
| 7 | complete | ACP client-backed filesystem and terminal helpers |

## Milestone 7 Phases

| Phase | Status | Scope |
| --- | --- | --- |
| 1 | complete | `ClientFilesystemBackend` |
| 2 | complete | `ClientTerminalBackend` |
| 3 | complete | `ClientHostContext` |

## Quick Start

```python
from pydantic_acp import run_acp
from pydantic_ai import Agent

agent = Agent("test", name="demo-agent")
run_acp(agent=agent)
```

Factory-backed usage:

```python
from pydantic_acp import AcpSessionContext, create_acp_agent
from pydantic_ai import Agent

def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    return Agent("test", name=f"demo-{session.cwd.name}")

acp_agent = create_acp_agent(agent_factory=build_agent)
```

## Major Features

- ACP session lifecycle support
- transcript and model-message history replay
- generic tool projection with ACP tool updates
- session-local models, modes, config options, and plan updates
- deferred approval bridging
- provider seams for host-owned state
- capability bridges for hooks, history processors, prepare-tools, and MCP
- session-scoped host backends

## Examples

See `examples/pydantic-acp/` in the workspace for structured SDK examples covering static agents,
factory-backed agents, providers, bridges, approvals, and host-context usage.

For fuller workspace documentation, see the root `README.md` and the `docs/` directory.
