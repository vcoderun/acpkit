---
name: acpkit-sdk
description: Use for ACP Kit SDK tasks that turn an existing agent surface into a truthful ACP server through acpkit, pydantic-acp, the published docs, and the maintained examples.
---

# ACP Kit SDK

ACP Kit is the adapter toolkit and monorepo for turning an existing agent surface into a truthful ACP server boundary.

Today the stable production focus is `pydantic-acp`: exposing `pydantic_ai.Agent` through ACP
while keeping models, modes, plans, approvals, MCP metadata, host tools, and session state
aligned with what the underlying runtime can actually support.

Additional adapters such as `langchain-acp` and `dspy-acp` are planned after `pydantic-acp`
reaches 1.0 stability.

This skill file is the longform, high-context entrypoint for the packaged `acpkit-sdk` skill and
should be treated as the primary skill surface when the skill is selected.

When you need the docs map or the full docs corpus in one place, read [llms.txt](https://vcoderun.github.io/acpkit/llms.txt) or [llms-full.txt](https://vcoderun.github.io/acpkit/llms-full.txt).

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

Current `FileSessionStore` behavior is tuned for durable local-host ACP use:

- atomic temp-file write plus replace
- local process lock and filesystem advisory lock when available
- malformed saved session files are skipped in public load/list flows
- stale temp files are cleaned up on startup

It is a strong default for editors and single-host services, not a distributed shared-state backend.

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

Use `AgentSource` when you need full control over both the agent and the dependency object:

```python
from dataclasses import dataclass

from pydantic_ai import Agent
from pydantic_acp import AcpSessionContext, AgentSource, AdapterConfig, run_acp


@dataclass(frozen=True, slots=True)
class Deps:
    workspace_name: str


class WorkspaceSource(AgentSource[Deps]):
    async def get_agent(self, session: AcpSessionContext) -> Agent[Deps, str]:
        return Agent(
            'openai:gpt-5',
            name='workspace-agent',
            instructions='Use the provided workspace dependencies.',
        )

    async def get_deps(self, session: AcpSessionContext) -> Deps:
        return Deps(workspace_name=session.cwd.name)


run_acp(
    agent_source=WorkspaceSource(),
    config=AdapterConfig(agent_name='workspace-agent'),
)
```

Reach for `AgentSource` in production-style examples where:

- session cwd matters
- model ids or modes come from the host
- tools depend on host-backed services
- you need a richer dependency object than the adapter can infer on its own

## Models, Modes, And Slash Commands

ACP Kit can expose session-local model and mode switching through two different ownership paths.

### Built-in model selection

Use built-in model selection when the adapter can own the available model set:

```python
from pydantic_acp import AdapterConfig, AdapterModel

config = AdapterConfig(
    allow_model_selection=True,
    available_models=[
        AdapterModel(
            model_id='fast',
            name='Fast',
            description='Lower latency.',
            override='openai:gpt-5-mini',
        ),
        AdapterModel(
            model_id='smart',
            name='Smart',
            description='Higher quality.',
            override='openai:gpt-5',
        ),
    ],
)
```

### Provider-owned model or mode state

Use providers when the host already owns that state:

- `SessionModelsProvider`
- `SessionModesProvider`
- `ConfigOptionsProvider`

Mode slash commands are dynamic. They are derived from the configured mode ids rather than from a
hard-coded global set.

Important guardrail:

- mode ids must not collide with reserved slash-command names such as `model`, `thinking`,
  `tools`, `hooks`, or `mcp-servers`

## Capability Bridges

Capability bridges are how ACP-visible runtime behavior gets added without hard-coding everything
into the adapter core.

Common bridges:

- `PrepareToolsBridge`
- `ThinkingBridge`
- `McpBridge`
- `HookBridge`
- `HistoryProcessorBridge`

Use `PrepareToolsBridge` to define dynamic modes and tool surfaces:

```python
from pydantic_acp import PrepareToolsBridge, PrepareToolsMode
from pydantic_ai.tools import RunContext, ToolDefinition


def ask_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return [tool_def for tool_def in tool_defs if not tool_def.name.startswith('write_')]


def agent_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return list(tool_defs)


bridge = PrepareToolsBridge(
    default_mode_id='ask',
    modes=[
        PrepareToolsMode(
            id='ask',
            name='Ask',
            description='Read-only inspection mode.',
            prepare_func=ask_tools,
        ),
        PrepareToolsMode(
            id='plan',
            name='Plan',
            description='Native ACP plan mode.',
            prepare_func=ask_tools,
            plan_mode=True,
        ),
        PrepareToolsMode(
            id='agent',
            name='Agent',
            description='Full tool surface.',
            prepare_func=agent_tools,
            plan_tools=True,
        ),
    ],
)
```

High-value guardrails:

- only one `PrepareToolsMode(..., plan_mode=True)` is allowed
- `plan_tools=True` is how a non-plan execution mode keeps plan progress tools visible
- `ThinkingBridge()` is what makes `/thinking` and the ACP-visible effort selector exist
- `HookBridge(hide_all=True)` suppresses hook listing output without removing the hook seam itself
- custom `run_event_stream` hooks or wrappers must return an async iterable; returning a coroutine will break stream execution

## Plans, Approvals, Cancellation, And Host Tools

Native ACP plan state is separate from provider-owned plan state.

Use native plan state when you want the adapter to own the ACP-visible plan lifecycle. Use
`PlanProvider` when the host should remain the source of truth.

Current plan behavior to remember:

- `plan_mode=True` exposes native ACP plan creation tools
- `plan_tools=True` lets execution modes keep plan progress tools visible
- native plan persistence can be mirrored outward through `NativePlanPersistenceProvider`

Approval behavior:

- `NativeApprovalBridge` powers the live ACP approval flow
- `ApprovalStateProvider` exposes extra approval metadata when the host already owns approval state

Cancellation behavior:

- ACP cancellation is wired through the runtime now
- cancellation preserves session state and transcript integrity instead of leaving the session in a broken partial state

Host-backed tools:

- ACP Kit can expose client-backed filesystem and shell helpers
- projection maps change how tools are rendered in ACP clients without changing the underlying tool contract

## Projection Maps And Hook Rendering

Projection maps make ACP clients see richer file or command behavior than a raw generic tool card.

Common maps:

- filesystem maps
- bash / command maps
- hook projection maps

Use them when the client should see more structured output, but avoid pretending a tool is
something it is not.

## Examples That Matter

High-value maintained examples live under `examples/pydantic/`:

| Example | Purpose |
| --- | --- |
| [`examples/pydantic/acp_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/acp_agent.py) | smallest direct `run_acp(...)` path |
| [`examples/pydantic/factory_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/factory_agent.py) | session-aware factory |
| [`examples/pydantic/providers.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/providers.py) | host-owned models, modes, config, plan, and approval metadata |
| [`examples/pydantic/approvals.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/approvals.py) | deferred approval flow |
| [`examples/pydantic/bridges.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/bridges.py) | bridge builder and ACP-visible capabilities |
| [`examples/pydantic/host_context.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/host_context.py) | client-backed filesystem and terminal helpers |
| [`examples/pydantic/hook_projection.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/hook_projection.py) | hook rendering and `HookProjectionMap` behavior |
| [`examples/pydantic/strong_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/strong_agent.py) | production-style workspace coding-agent showcase |
| [`examples/pydantic/strong_agent_v2.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/strong_agent_v2.py) | alternative workspace integration shape |

Use [`strong_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/strong_agent.py) when the docs or code need to highlight:

- mode-aware tool shaping
- provider-owned model and mode state
- native plan persistence
- host-backed tools
- MCP metadata mapping
- bridge composition
- projection maps
- final `create_acp_agent(...)` assembly

## Documentation Sources

High-value docs pages:

- [ACP Kit Overview](https://vcoderun.github.io/acpkit/)
- [Pydantic ACP Overview](https://vcoderun.github.io/acpkit/pydantic-acp/)
- [AdapterConfig](https://vcoderun.github.io/acpkit/pydantic-acp/adapter-config/)
- [Session State and Lifecycle](https://vcoderun.github.io/acpkit/pydantic-acp/session-state/)
- [Models, Modes, and Slash Commands](https://vcoderun.github.io/acpkit/pydantic-acp/runtime-controls/)
- [Plans, Thinking, and Approvals](https://vcoderun.github.io/acpkit/pydantic-acp/plans-thinking-approvals/)
- [Providers](https://vcoderun.github.io/acpkit/providers/)
- [Bridges](https://vcoderun.github.io/acpkit/bridges/)
- [Host Backends and Projections](https://vcoderun.github.io/acpkit/host-backends/)
- [Examples Overview](https://vcoderun.github.io/acpkit/examples/)
- [Workspace Agent](https://vcoderun.github.io/acpkit/examples/workspace-agent/)
- [pydantic_acp API](https://vcoderun.github.io/acpkit/api/pydantic_acp/)

## Skill-Local Routing Aids

These files exist to route you quickly into the right part of the codebase or docs set when the
task is narrow:

- [resources/intro.md](resources/intro.md)
- [references/package-surface.md](references/package-surface.md)
- [references/runtime-capabilities.md](references/runtime-capabilities.md)
- [references/docs-examples-map.md](references/docs-examples-map.md)

## Utility Scripts

Use the bundled scripts instead of guessing:

- `python3.11 .agents/skills/acpkit-sdk/scripts/list_public_exports.py`
- `python3.11 .agents/skills/acpkit-sdk/scripts/list_examples.py`

## Working Rules

- Prefer current code over stale memory.
- If docs and code disagree, trust code first and update docs.
- Do not invent ACP surface the runtime cannot actually honor.
- Keep examples runnable, explicit, and strongly typed.
- Treat adapter-owned state and host-owned state as different design choices.
- Prefer the narrowest seam that matches the user’s need.
- `FileSessionStore` uses `root=Path(...)`.
- `FileSessionStore` is the hardened local durable store, not a distributed session backend.
- Mode slash commands are dynamic, and mode ids must not collide with reserved names such as `model`, `thinking`, `tools`, `hooks`, or `mcp-servers`.
- `run_event_stream` hooks must return async iterables, not coroutines.
