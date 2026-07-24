# Pydantic ACP Overview

`pydantic-acp` is the primary Pydantic AI adapter in ACP Kit.

Its job is simple: keep your existing `pydantic_ai.Agent` surface intact, then expose it as an ACP server without inventing runtime state the underlying agent cannot actually honor.

Use it when you want ACP-native clients to see truthful:

- models and model switching
- modes and slash commands
- native plan state and plan progress
- approval workflows
- cancellation behavior
- MCP metadata and host-backed tools
- harness-backed filesystem, shell, and optional CodeMode capability surfaces
- prompt resources such as editor selections, branch diffs, file references, and multimodal input
- persisted ACP sessions and replayable transcript state

## The Main Server Integration Seams

Most server-side integrations use one of these seams.

### `run_acp(...)`

Use `run_acp(...)` when you already have an agent instance and want the smallest supported ACP entrypoint:

```python
from pydantic_ai import Agent
from pydantic_acp import run_acp

agent = Agent("openai:gpt-5", name="demo-agent")

run_acp(agent=agent)
```

This is the fastest path from a normal `pydantic_ai.Agent` to a working ACP server.

If the agent should reuse an existing local Codex login, build the model through
`codex-auth-helper` and pass explicit instructions at factory construction time:

```python
from codex_auth_helper import create_codex_responses_model
from pydantic_ai import Agent

model = create_codex_responses_model(
    "gpt-5.4",
    instructions="You are a helpful coding assistant.",
)

agent = Agent(model, name="codex-agent")
```

On the Pydantic path, `Agent(instructions=...)` can still be layered on top for
agent-owned instructions, but the Codex factory should always receive explicit
`instructions=...`.

### `create_acp_agent(...)`

Use `create_acp_agent(...)` when another runtime should own transport lifecycle but you still want the adapter assembly:

```python
from pydantic_ai import Agent
from pydantic_acp import create_acp_agent

agent = Agent("openai:gpt-5", name="demo-agent")
acp_agent = create_acp_agent(agent=agent)
```

This is the lower-level construction seam behind `run_acp(...)`.

## ACP Client Provider Bridge

Use `create_acp_model(...)` when the thing you already have is an ACP agent or
ACP stdio command and the host application needs to consume it through normal
Pydantic AI v2 model APIs.

This is the inverse of `create_acp_agent(...)`:

- `create_acp_agent(...)` exposes a `pydantic_ai.Agent` as ACP
- `create_acp_model(...)` consumes an ACP agent as a Pydantic AI model

```python
from pydantic_ai import Agent
from pydantic_acp import create_acp_agent, create_acp_model

inner_acp = create_acp_agent(agent=some_pydantic_agent)
model = create_acp_model(acp_agent=inner_acp, cwd="/workspace")
agent = Agent(model)

result = await agent.run("Summarize the current workspace state.")
print(result.output)
```

`acp_command` is for child processes that speak ACP JSON-RPC on stdin/stdout.
It is not an arbitrary CLI wrapper:

```python
from pydantic_ai import Agent
from pydantic_acp import create_acp_model

model = create_acp_model(
    acp_command=("npx", "@zed-industries/codex-acp"),
    cwd="/workspace",
    stderr_mode="inherit",
    raise_on_empty_turn=True,
)
agent = Agent(model)
```

`create_acp_model(...)` intentionally leaves ACP model selection to the wrapped
agent's session default. Pass `model_name="zed-agent"` only when the wrapped
ACP agent exposes a selectable `"model"` `session/set_config_option` option.
`AcpProvider` and
`AcpModel` remain available when lower-level provider ownership is needed.

For command-backed and in-process ACP agents, session setup is explicit and
does not require a dummy prompt:

```python
from acp.interfaces import Agent as AcpAgent
from pydantic_acp import AcpProvider


async def prepare_provider(acp_agent: AcpAgent) -> AcpProvider:
    provider = AcpProvider(
        acp_agent=acp_agent,
        cwd="/workspace",
        raise_on_empty_turn=True,
    )
    await provider.ensure_session()
    await provider.set_session_mode("review")
    return provider
```

If `session/new` fails with ACP `auth_required`, `AcpProvider` calls
`authenticate` with the first advertised `AuthMethodAgent` and retries session
creation once. `auth_method_id="..."` selects a specific method when the host
has prepared it. `EnvVarAuthMethod` and `TerminalAuthMethod` require
client-owned credential injection or terminal execution, so the provider does
not select them automatically.

Agent errors are propagated without single-child anyio TaskGroup wrapping.
`raise_on_empty_turn=True` additionally turns a text request that produces no
visible agent text into `UnexpectedModelBehavior`; the default remains `False`
for backward compatibility.

The bridge keeps ownership explicit:

- Pydantic AI owns the outer run, output validation, and normal provider
  lifecycle.
- ACP owns the delegated session, ACP-visible updates, and any editor or host
  capabilities requested by the wrapped agent.
- Pydantic AI function tools are not executed directly by `AcpModel`; register
  tools on the ACP agent or expose host capabilities through ACP.
- `AcpHostBridge` records ACP `session_update` messages and can delegate
  filesystem, terminal, approval, and extension callbacks to a real ACP host
  client.

### `agent_factory=...`

Use `agent_factory=` when the session should influence which agent gets built, but a full custom
`AgentSource` would be unnecessary:

```python
from pydantic_ai import Agent
from pydantic_acp import AcpSessionContext, AdapterConfig, MemorySessionStore, run_acp


def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    workspace_name = session.cwd.name
    tenant = str(session.metadata.get("tenant", "general"))
    model_name = "openai:gpt-5.4-mini"
    if workspace_name.endswith("-deep"):
        model_name = "openai:gpt-5.4"

    return Agent(
        model_name,
        name=f"{tenant}-{workspace_name}",
        system_prompt=f"Work inside {workspace_name} for tenant {tenant}.",
    )


run_acp(
    agent_factory=build_agent,
    config=AdapterConfig(session_store=MemorySessionStore()),
)
```

This is the right seam when:

- the model should change by workspace or tenant
- the prompt or instructions should change from session metadata
- the adapter should build one session-specific `Agent(...)` instance per ACP session

If the agent also needs separately-constructed session dependencies, use `AgentSource` instead.

### `AgentSource`

Use `AgentSource` when agent construction depends on session state, request context, or host-owned dependencies:

```python
from pydantic_acp import AgentSource


class WorkspaceAgentSource(AgentSource[MyDeps]):
    async def get_agent(self, session):
        ...

    async def get_deps(self, session):
        ...
```

This is the right seam for provider-backed sessions, workspace-aware coding agents, and host-owned dependency injection.

## What The Adapter Owns

By default, the adapter can own:

- ACP session persistence
- transcript and message-history replay
- built-in model selection
- built-in mode selection
- native ACP plan state
- thinking effort config
- approval flow through an approval bridge
- projection-aware permission prompt rendering and remembered approval policies
- generic or rich projected tool rendering
- host-defined slash commands and prompt capability advertisement

The built-in ownership path is usually enough for:

- internal tools
- local development
- single-tenant ACP agents
- examples and demos

## What The Host Can Own

When your product already has a source of truth, keep that ownership in the host and expose it through providers.

Common provider seams:

- `SessionModelsProvider`
- `SessionModesProvider`
- `ConfigOptionsProvider`
- `PlanProvider`
- `ApprovalStateProvider`
- `NativePlanPersistenceProvider`

Use providers when:

- model ids come from product policy
- mode state is product-owned
- plans must be mirrored into your own storage
- approval metadata already exists elsewhere
- the adapter should expose state, not create it

## Bridges: How ACP-visible Behavior Gets Added

Capability bridges are how the adapter contributes ACP-facing runtime behavior.

Common bridges:

- `PrepareToolsBridge`
  exposes dynamic modes, plan tools, and tool-surface filtering
- `ThinkingBridge`
  exposes ACP-visible thinking effort when the model runtime supports it
- `NativeApprovalBridge`
  powers ACP approval workflows
- `McpBridge`
  exposes MCP metadata and config options
- `SessionMcpBridge`
  converts ACP client-provided `session/new.mcpServers` payloads into real Pydantic AI MCP toolsets
- `HookBridge`
  exposes or suppresses hook activity
- `HistoryProcessorBridge`
  lets the host rewrite or enrich message history

The important rule is that bridges should describe real runtime behavior, not hypothetical UI affordances.

Harness-backed capability bridges follow the same rule. Use:

- `HarnessFileSystemBridge` for workspace-scoped file tools
- `HarnessShellBridge` for bounded shell tools
- `HarnessCodeModeBridge` only when the run should expose CodeMode execution tools

When you use those bridges, pair them with:

- `HarnessFileSystemProjectionMap`
- `HarnessShellProjectionMap`
- `HarnessCodeModeProjectionMap`

That combination keeps ACP transcript updates readable and tool-family-specific. The maintained guide
for this surface is:

- [Harness-backed Capabilities](https://github.com/vcoderun/acpkit/blob/main/docs/pydantic-acp/harness-capabilities.md)

## Runtime Notes

- `Agent(output_type=str | None)` is supported, but a successful `None` result ends the turn without emitting a synthetic `"null"` transcript message.

## A Production-shaped Configuration

```python
from pathlib import Path

from pydantic_ai import Agent
from pydantic_acp import (
    AdapterConfig,
    FileSessionStore,
    NativeApprovalBridge,
    PrepareToolsBridge,
    PrepareToolsMode,
    ThinkingBridge,
    run_acp,
)

agent = Agent("openai:gpt-5", name="workspace-agent")

config = AdapterConfig(
    session_store=FileSessionStore(root=Path(".acp-sessions")),
    approval_bridge=NativeApprovalBridge(enable_persistent_choices=True),
    capability_bridges=[
        ThinkingBridge(),
        PrepareToolsBridge(
            default_mode_id="ask",
            modes=[
                PrepareToolsMode(
                    id="ask",
                    name="Ask",
                    description="Read-only inspection mode.",
                    prepare_func=lambda ctx, tool_defs: list(tool_defs),
                ),
                PrepareToolsMode(
                    id="plan",
                    name="Plan",
                    description="Native ACP plan mode.",
                    prepare_func=lambda ctx, tool_defs: list(tool_defs),
                    plan_mode=True,
                ),
            ],
        ),
    ],
)

run_acp(agent=agent, config=config)
```

This is not the only valid shape, but it shows the real moving parts:

- `FileSessionStore` persists ACP session state
- `NativeApprovalBridge` enables approvals
- `ThinkingBridge` exposes effort selection
- `PrepareToolsBridge` defines ACP-visible modes and plan behavior

## Recommended Reading Order

If you are integrating `pydantic-acp` in a real product:

1. Read [Pydantic Quickstart](getting-started/pydantic-quickstart.md).
2. Read [AdapterConfig](pydantic-acp/adapter-config.md).
3. Read [Models, Modes, and Slash Commands](pydantic-acp/runtime-controls.md).
4. Read [Plans, Thinking, and Approvals](pydantic-acp/plans-thinking-approvals.md).
5. Read [Harness-backed Capabilities](https://github.com/vcoderun/acpkit/blob/main/docs/pydantic-acp/harness-capabilities.md) if your agent should expose `pydantic-ai-harness` filesystem, shell, or CodeMode tools.
6. Read [Prompt Resources and Context](pydantic-acp/prompt-resources.md) if your client attaches selections, diffs, file refs, or multimodal input.
7. Read [Providers](providers.md) if the host already owns state.
8. Read [Bridges](bridges.md) if you need ACP-visible runtime extensions.
9. Read [Finance Agent](examples/finance.md) and [Travel Agent](examples/travel.md) for maintained end-to-end examples.

## Common Mistakes

- Treating ACP as a separate agent implementation instead of an adapter layer over your existing agent surface
- letting the adapter advertise UI state the runtime cannot really honor
- mixing built-in state ownership and provider ownership without a clear source of truth
- assuming plan tools exist in every mode instead of explicitly enabling `plan_mode` or `plan_tools`
- using `FileSessionStore(base_dir=...)` instead of `FileSessionStore(root=...)`
- treating `FileSessionStore` like a distributed multi-writer backend instead of a hardened local durable store
- returning a coroutine from `run_event_stream` hooks instead of an async iterable

## Version Compatibility And Private Upstream APIs

`pydantic-acp` supports `pydantic-ai-slim>=2.9.0,<=2.16.0`. Pydantic AI V1 and
Pydantic AI 2.x releases before 2.9.0 are outside the supported range.

Each supported minor is checked against the same adapter runtime suite and
Pydantic-specific type-check scope. Run the matrix locally with:

```bash
make check-pydantic-ai-matrix
```

The current compatibility surface includes function-tool preparation,
output-tool preparation, output validation/processing hooks,
deferred-tool-call hooks, run metadata, and conversation IDs.

Pydantic AI V2 defaults the agent dependency and output generic parameters to
`object`. If your agent contract explicitly uses `RunContext[None]` or
`Hooks[None]`, declare the dependency type instead of relying on the old V1
default:

```python
from pydantic_ai import Agent

agent: Agent[None, str] = Agent(
    "openai:gpt-5",
    deps_type=type(None),
    name="typed-agent",
)
```

The adapter consumes `run_stream_events()` as an async context manager across
the supported range. Event iteration starts the run lazily; callers do not need
a version branch.

ACP Kit also no longer imports Pydantic AI private history-processor modules
directly. History processor support is expressed through ACP Kit's own callable
aliases and wrapped as `ProcessHistory` capabilities inside
`contributions.capabilities`.

What this means in practice:

- the adapter is less exposed to private upstream type-module churn
- Pydantic AI 2.9.0 through 2.16.0 share one public adapter contract
- future Pydantic AI upgrades remain explicit compatibility work
- integration points stay isolated behind ACP Kit bridge and runtime seams
