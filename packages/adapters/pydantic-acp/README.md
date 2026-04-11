# pydantic-acp

`pydantic-acp` adapts `pydantic_ai.Agent` instances to the ACP agent interface.

## Entry Points

- `run_acp(...)`
- `create_acp_agent(...)`
- `AdapterConfig`
- `AcpSessionContext`
- `MemorySessionStore`
- `FileSessionStore`

## What It Covers

`pydantic-acp` includes:

- ACP session lifecycle and replay
- session-local model control
- providers for host-owned models, modes, config options, and plans
- native deferred approval bridging
- projection maps for filesystem diffs and bash previews
- capability bridges for hooks, history processors, prepare-tools, and MCP metadata
- hook introspection and `HookProjectionMap`
- client-backed filesystem and terminal helpers

## Compatibility Policy

`pydantic-acp` currently pins `pydantic-ai-slim==1.73.0`.

That pin is still deliberate, but the adapter no longer imports Pydantic AI
private history-processor modules directly. ACP Kit defines its own
history-processor callable aliases and wires them into the public
`Agent(..., history_processors=...)` surface.

Practical implication:

- upgrades should still be treated as deliberate compatibility work
- ACP Kit is no longer coupled to `pydantic_ai._history_processor` imports
- history processor integrations should use ACP Kit's exported aliases or plain
  callable functions, not upstream private modules

Slash commands are available for:

- `/model`
- `/tools`
- `/hooks`
- `/mcp-servers`

## Quick Start

```python
from pydantic_ai import Agent
from pydantic_acp import run_acp

agent = Agent("openai:gpt-5", name="demo-agent")
run_acp(agent=agent)
```

## Configured Runtime

```python
from pathlib import Path

from pydantic_ai import Agent
from pydantic_acp import (
    AdapterConfig,
    FileSessionStore,
    NativeApprovalBridge,
    run_acp,
)

agent = Agent("openai:gpt-5", name="configured-agent")

run_acp(
    agent=agent,
    config=AdapterConfig(
        session_store=FileSessionStore(root=Path(".acp-sessions")),
        approval_bridge=NativeApprovalBridge(enable_persistent_choices=True),
    ),
)
```

## Projection Maps

Filesystem projection:

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

Hook projection:

```python
from pydantic_acp import HookProjectionMap, run_acp

run_acp(
    agent=agent,
    projection_maps=(
        HookProjectionMap(
            hidden_event_ids=frozenset({"after_model_request"}),
            event_labels={"before_tool_execute": "Starting Tool"},
        ),
    ),
)
```

## Factories, Providers, And Host Backends

Use `agent_factory` or `AgentSource` when the session context should influence agent creation.
Use providers when models, modes, config options, or plans belong to the host layer. Use
`ClientHostContext` when tools should talk back to the ACP client's filesystem or terminal.

## Examples

See [examples/pydantic/README.md](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/README.md) for
focused SDK examples and the full runnable demo.

Key examples:

- [examples/pydantic/static_agent.py](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/static_agent.py)
- [examples/pydantic/hook_projection.py](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/hook_projection.py)
- [examples/pydantic/strong_agent.py](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/strong_agent.py)

For full workspace documentation, see:

- [README.md](https://github.com/vcoderun/acpkit/blob/main/README.md)
- [Pydantic ACP Overview](https://vcoderun.github.io/acpkit/pydantic-acp/)
