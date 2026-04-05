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
- hook projection and existing-hook introspection
- host backends

## Implemented Scope

- Milestone 1-7 scope is implemented
- the adapter includes session lifecycle, session-local model control, providers, approvals, bridges, projection maps, and host backends
- slash commands are available for `/model`, `/tools`, `/hooks`, and `/mcp-servers`

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

File-backed session persistence:

```python
from pathlib import Path

from pydantic_acp import AdapterConfig, FileSessionStore, run_acp

run_acp(
    agent=agent,
    config=AdapterConfig(
        session_store=FileSessionStore(base_dir=Path(".acp-sessions")),
    ),
)
```

Projection map example:

```python
from pydantic_acp import FileSystemProjectionMap, run_acp

run_acp(
    agent=agent,
    projection_maps=(
        FileSystemProjectionMap(
            default_read_tool="read_file",
            default_write_tool="write_file",
            default_bash_tool="execute",
        ),
    ),
)
```

Hook projection example:

```python
from pydantic_acp import AdapterConfig, HookProjectionMap, run_acp

run_acp(
    agent=agent,
    config=AdapterConfig(
        hook_projection_map=HookProjectionMap(
            hidden_event_ids=frozenset({"after_model_request"}),
        )
    ),
)
```

## Major Features

- ACP session lifecycle support
- transcript and model-message history replay
- generic tool projection with ACP tool updates
- projection maps for filesystem diffs and bash command previews
- session-local models, modes, config options, and plan updates
- deferred approval bridging
- provider seams for host-owned state
- capability bridges for hooks, history processors, prepare-tools, and MCP
- hook introspection and `HookProjectionMap` rendering for existing `Hooks` capabilities
- session-scoped host backends

## Examples

See `examples/pydantic/` for focused SDK examples covering static agents, factories, providers,
bridges, approvals, host-context usage, and hook projection.

Key entry points:

- `examples/pydantic/hook_projection.py`
- `examples/pydantic/my_agent.py`

For fuller workspace documentation, see the root `README.md` and the `docs/` directory.
