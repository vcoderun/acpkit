---
name: acpkit-sdk
description: Use for ACP Kit SDK tasks that turn an existing agent surface into a truthful ACP server through acpkit, pydantic-acp, and the maintained docs/examples.
---

# ACP Kit SDK

ACP Kit is a Python SDK and CLI for turning an existing agent surface into a truthful ACP server boundary.

Today that mostly means exposing `pydantic_ai.Agent` through `pydantic-acp`, while keeping models,
modes, plans, approvals, MCP metadata, host tools, and session state aligned with what the
underlying runtime can actually support.

This root `SKILL.md` is the longform, one-file reference. The canonical orchestration skill package
still lives under `.agents/skills/acpkit-sdk/`, but this file should remain self-contained enough
to use on its own.

## What ACP Kit Ships

ACP Kit currently has three main Python packages:

| Package | Role | Typical use |
| --- | --- | --- |
| `acpkit` | root CLI and target resolver | `acpkit run ...`, `acpkit launch ...`, target loading |
| `pydantic-acp` | ACP adapter for `pydantic_ai.Agent` | expose an existing agent through ACP without rewriting it |
| `codex-auth-helper` | Codex-backed Pydantic AI helper | build Codex-backed Responses models from a local Codex login |

The core contract across the repo is:

> expose ACP state only when the underlying runtime can actually honor it.

That rule affects model selection, mode switching, slash commands, native plan state, approval
flow, MCP metadata, hook rendering, cancellation, and host-backed tooling.

## Use The Right Construction Seam

Pick the narrowest seam that matches the job:

| Seam | Use it when |
| --- | --- |
| `run_acp(agent=...)` | you want the smallest direct path from `pydantic_ai.Agent` to a running ACP server |
| `create_acp_agent(...)` | you need the ACP-compatible agent object before running it |
| `agent_factory=` | the current session should influence agent construction, but a full custom source is unnecessary |
| `agent_source=` | you need full control over the agent build path, host binding, and session-specific dependencies |
| built-in `AdapterConfig` fields | the adapter can own the relevant runtime state cleanly |
| providers | the host or product layer should remain the source of truth |
| bridges | the runtime needs ACP-visible capabilities without hard-coding them into the adapter core |

## CLI And Target Resolution

The root `acpkit` package resolves Python targets and dispatches them to the matching adapter.

```bash
acpkit run my_agent
acpkit run my_agent:agent
acpkit run app.agents.demo:agent -p ./examples
acpkit launch my_agent:agent -p ./examples
acpkit launch --command "python3.11 strong_agent.py"
```

Target resolution behavior:

1. add the current working directory to `sys.path`
2. add any `-p/--path` roots
3. import the requested module
4. if `module:attribute` was given, resolve the attribute path
5. if only `module` was given, select the last defined `pydantic_ai.Agent` in that module

Current built-in auto-dispatch support is centered on `pydantic_ai.Agent`.

## Smallest Adapter Path: `run_acp(...)`

Use `run_acp(...)` when one existing agent instance is enough.

```python
from pydantic_ai import Agent
from pydantic_acp import run_acp

agent = Agent(
    'openai:gpt-5',
    name='weather-agent',
    instructions='Answer briefly and ask for clarification when location is missing.',
)


@agent.tool_plain
def lookup_weather(city: str) -> str:
    """Return a canned weather response for demos."""

    return f'Weather in {city}: sunny'


run_acp(agent=agent)
```

This is the narrowest path from an existing agent surface to a live ACP server.

## ACP Agent Without Running: `create_acp_agent(...)`

Use `create_acp_agent(...)` when you need the ACP-compatible agent object first.

```python
from acp import run_agent
from pydantic_ai import Agent
from pydantic_acp import AdapterConfig, MemorySessionStore, create_acp_agent

agent = Agent('openai:gpt-5', name='composable-agent')

acp_agent = create_acp_agent(
    agent=agent,
    config=AdapterConfig(
        agent_name='my-service',
        agent_title='My Service Agent',
        session_store=MemorySessionStore(),
    ),
)

# later:
# await run_agent(acp_agent)
```

Use this seam when ACP is only one part of a larger async service boundary.

## `AdapterConfig` Is The Main Runtime Surface

`AdapterConfig` is where the adapter’s built-in ownership lives:

- session storage
- model selection
- approval bridging
- capability bridges
- plan persistence callbacks
- hook projection and runtime shaping

```python
from pathlib import Path

from pydantic_ai import Agent
from pydantic_acp import (
    AdapterConfig,
    AdapterModel,
    FileSessionStore,
    NativeApprovalBridge,
    ThinkingBridge,
    run_acp,
)

agent = Agent('openai:gpt-5', name='configured-agent')

config = AdapterConfig(
    agent_name='my-agent',
    agent_title='My Agent Title',
    allow_model_selection=True,
    available_models=[
        AdapterModel(
            model_id='fast',
            name='Fast',
            description='Lower-latency responses.',
            override='openai:gpt-5-mini',
        ),
        AdapterModel(
            model_id='smart',
            name='Smart',
            description='Higher-quality responses.',
            override='openai:gpt-5',
        ),
    ],
    capability_bridges=[ThinkingBridge()],
    approval_bridge=NativeApprovalBridge(enable_persistent_choices=True),
    session_store=FileSessionStore(root=Path('.acp-sessions')),
)

run_acp(agent=agent, config=config)
```

High-value detail:

- `FileSessionStore` takes `root=Path(...)`, not `base_dir=...`

## Session Stores

ACP Kit currently ships two session stores:

- `MemorySessionStore`
- `FileSessionStore`

`MemorySessionStore` is ephemeral. `FileSessionStore` persists ACP sessions across restarts and
supports:

- save
- get
- list
- fork
- delete

Use `FileSessionStore(root=Path(...))` when real ACP clients need durable session state.

## Agent Factories And `AgentSource`

Use an agent factory when session context changes agent construction but you do not need a full
custom source object.

```python
from pydantic_ai import Agent
from pydantic_acp import AcpSessionContext, AdapterConfig, run_acp


def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    workspace_name = session.cwd.name
    return Agent(
        'openai:gpt-5',
        name=f'agent-{workspace_name}',
        instructions=f'You are working in {workspace_name}.',
    )


run_acp(
    agent_factory=build_agent,
    config=AdapterConfig(agent_name='factory-agent'),
)
```

Reach for `agent_source=` when you need full control over:

- agent construction
- session-scoped dependencies
- host binding
- bridge composition
- workspace-specific tools

This is the seam used by the workspace coding-agent examples.

## Models, Modes, And Slash Commands

ACP Kit can expose model and mode state only when it is real.

Model selection can be owned by:

- built-in `AdapterConfig(allow_model_selection=True, available_models=[...])`
- `SessionModelsProvider`

Mode state can be owned by:

- `PrepareToolsBridge`
- `SessionModesProvider`

Important current rules:

- slash mode commands are dynamic; `ask`, `plan`, and `agent` are examples, not built-in global names
- mode ids must be slash-command compatible
- mode ids must not collide with reserved names such as `model`, `thinking`, `tools`, `hooks`, or `mcp-servers`
- `/thinking` only exists when `ThinkingBridge()` is configured

Today the fixed slash command surfaces include:

- `/model`
- `/thinking`
- `/tools`
- `/hooks`
- `/mcp-servers`

The mode commands come from actual mode state, not from a hard-coded list.

## Native Plan State

Native ACP plan support is separate from `PlanProvider`.

Native plan tool levels:

- plan access:
  - `acp_get_plan`
  - `acp_set_plan`
- plan progress:
  - `acp_update_plan_entry`
  - `acp_mark_plan_done`

Important runtime details:

- one `PrepareToolsMode(..., plan_mode=True)` may exist; it is singular
- `plan_tools=True` keeps progress tools visible in a non-plan execution mode
- plan entry numbering is intentionally 1-based
- native ACP plan state and `PlanProvider` are separate ownership paths
- `NativePlanPersistenceProvider` persists adapter-owned native plan state; it is not the same thing as `PlanProvider`

## Thinking, Approvals, And Cancellation

`ThinkingBridge()` exposes Pydantic AI’s native `Thinking` capability as ACP-visible session-local
state.

Approval flow is handled by `ApprovalBridge`, usually `NativeApprovalBridge`.

Do not conflate:

- live approval handling
- approval metadata shown in session state

Cancellation is a real runtime path, not a no-op. Current behavior:

- active prompt tasks are cancelled
- session history stays coherent
- transcript receives a cancellation note
- prompt responses report `stop_reason='cancelled'`

## Capability Bridges

Bridges make runtime features ACP-visible without hard-coding product-specific assumptions into the
adapter core.

High-value bridge surface:

- `PrepareToolsBridge`
- `PrepareToolsMode`
- `ThinkingBridge`
- `HookBridge`
- `HistoryProcessorBridge`
- `McpBridge`
- `McpServerDefinition`
- `McpToolDefinition`

Use them for:

- mode-aware tool shaping
- slash-command-visible runtime state
- MCP metadata
- history processor registration
- hook visibility and session metadata

`HookBridge(hide_all=True)` suppresses hook listing output. It does not disable the underlying hook
capability.

## Providers: Host-Owned State

Providers let the host remain the source of truth while the adapter exposes that state through ACP.

High-value provider seams:

- `SessionModelsProvider`
- `SessionModesProvider`
- `ConfigOptionsProvider`
- `PlanProvider`
- `NativePlanPersistenceProvider`
- `ApprovalStateProvider`

Use providers when the adapter should not own:

- current model selection
- available modes
- config options
- plan state
- approval metadata

This is the correct pattern for product integrations where session state already exists outside the
adapter.

## Host Backends And Projections

For workspace-style agents ACP Kit can expose:

- client-backed filesystem reads and writes
- client-backed terminal execution
- projection maps for ACP-friendly rendering of file and shell operations

High-value types:

- `ClientHostContext`
- `ClientFilesystemBackend`
- `ClientTerminalBackend`
- `FileSystemProjectionMap`
- `HookProjectionMap`

Common pattern:

- repo tools stay deterministic and plain
- host tools are only added when a real host context is bound
- mutating host tools typically require approval

## Codex Helper

`codex-auth-helper` is the supported path for Codex-backed Pydantic AI Responses models.

Use it when:

- examples should run with a local Codex login
- product code needs a Codex-backed `ResponsesModel`
- workspace agent examples should stay aligned with the supported Codex integration path

Current assumption:

- the local environment already has a Codex login available

## Example Ladder

When the user wants code, start from the maintained examples instead of inventing a new shape.

| Example | Intended lesson |
| --- | --- |
| `examples/pydantic/static_agent.py` | smallest direct `run_acp(agent=...)` integration |
| `examples/pydantic/factory_agent.py` | session-aware factory |
| `examples/pydantic/providers.py` | host-owned models, modes, config, plan, and approval metadata |
| `examples/pydantic/approvals.py` | deferred approval flow |
| `examples/pydantic/bridges.py` | bridge builder and ACP-visible capabilities |
| `examples/pydantic/host_context.py` | client-backed filesystem and terminal helpers |
| `examples/pydantic/hook_projection.py` | hook rendering and `HookProjectionMap` behavior |
| `examples/pydantic/strong_agent.py` | production-style workspace coding-agent showcase |
| `examples/pydantic/strong_agent_v2.py` | alternative workspace integration shape |

Use `strong_agent.py` when the docs or code need to highlight:

- mode-aware tool shaping
- provider-owned model and mode state
- native plan persistence
- host-backed tools
- MCP metadata mapping
- bridge composition
- projection maps
- final `create_acp_agent(...)` assembly

## Documentation Sources

The published docs base URL is:

- `https://vcoderun.github.io/acpkit/`

High-value docs pages:

- `docs/index.md`
- `docs/pydantic-acp.md`
- `docs/pydantic-acp/adapter-config.md`
- `docs/pydantic-acp/session-state.md`
- `docs/pydantic-acp/runtime-controls.md`
- `docs/pydantic-acp/plans-thinking-approvals.md`
- `docs/providers.md`
- `docs/bridges.md`
- `docs/host-backends.md`
- `docs/examples/index.md`
- `docs/examples/workspace-agent.md`
- `docs/api/pydantic_acp.md`

Use the root `SKILL.md` when you want the one-file longform reference. Use
`.agents/skills/acpkit-sdk/` when you want the lighter orchestration entrypoint plus helper
references.

## Working Rules

- Prefer current code over stale memory.
- If docs and code disagree, trust code first and update docs.
- Do not invent ACP surface the runtime cannot actually honor.
- Keep examples runnable, explicit, and strongly typed.
- Treat adapter-owned state and host-owned state as different design choices.
- Prefer the narrowest seam that matches the user’s need.
