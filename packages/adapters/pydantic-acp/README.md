# pydantic-acp

`pydantic-acp` adapts `pydantic_ai.Agent` instances to the ACP agent interface without rewriting the underlying agent.

Install the stable v1 package with optional harness integration:

```bash
uv add "pydantic-acp[harness]>=1.0.0,<2.0.0"
```

```bash
pip install "pydantic-acp[harness]>=1.0.0,<2.0.0"
```

The core contract is simple:

1. keep the existing `pydantic_ai.Agent`
2. expose it through ACP
3. only publish ACP-visible state the runtime can actually honor

The package also includes the inverse bridge: an ACP agent can be consumed as a Pydantic AI v2 provider/model pair via `AcpProvider` and `AcpModel`.
The final v1 stability boundary is defined by the
[ACP Kit versioning policy](https://vcoderun.github.io/acpkit/versioning/).

## Entry Points

- `run_acp(...)`
- `create_acp_agent(...)`
- `AdapterConfig`
- `AcpSessionContext`
- `StaticAgentSource`
- `FactoryAgentSource`
- `AcpProvider`
- `AcpModel`
- `AcpHostBridge`

## What It Covers

`pydantic-acp` includes:

- ACP session lifecycle, replay, resume, and persistence
- session-local model selection
- mode and slash-command control
- native ACP plan state with structured `TaskPlan`
- approval bridging
- prompt resources including files, embedded resources, images, and audio
- projection maps for filesystem, hooks, web tools, and builtin tool families
- capability bridges for upstream Pydantic AI capabilities
- client-backed filesystem and terminal helpers
- ACP client/provider bridging for consuming ACP agents from the Pydantic AI ecosystem

## Quick Start

Expose a Pydantic AI agent through ACP:

```python
from pydantic_ai import Agent
from pydantic_acp import run_acp

agent = Agent("openai:gpt-5", name="demo-agent")
run_acp(agent=agent)
```

## ACP 0.11 Controls

The adapter targets `agent-client-protocol==0.11.0`. Model changes use the
selectable `"model"` session config option rather than the removed
`session/set_model` RPC. `AdapterConfig(plan_update_mode="content")` emits
incremental plan updates only for clients that advertise plan support and
otherwise falls back to complete plan updates.

`additional_directories` are durable session roots for client-backed file and
terminal bridges, subject to any explicit `workspace_root` policy. Typed
elicitation is available through `AcpSessionContext.create_elicitation(...)`.

`AcpMcpServer` session descriptors are retained for host-owned ACP delegation;
they are not connected or advertised as an ACP MCP transport because the SDK
does not currently expose a public router for it. Use HTTP, SSE, or stdio MCP
servers when `SessionMcpBridge` must execute tools.

If another runtime should own transport lifecycle:

```python
from acp import run_agent
from pydantic_ai import Agent
from pydantic_acp import AdapterConfig, MemorySessionStore, create_acp_agent

agent = Agent("openai:gpt-5", name="composable-agent")

acp_agent = create_acp_agent(
    agent=agent,
    config=AdapterConfig(session_store=MemorySessionStore()),
)

run_agent(acp_agent)
```

## ACP Client Provider Bridge

`create_acp_model(...)` is the client-side mirror of the ACP server adapter. It wraps an ACP agent, or an ACP stdio command, as a Pydantic AI v2 model. `AcpProvider` and `AcpModel` remain available when lower-level provider ownership is needed.

```python
from pydantic_ai import Agent
from pydantic_acp import create_acp_agent, create_acp_model

inner_acp = create_acp_agent(agent=some_pydantic_agent)
model = create_acp_model(acp_agent=inner_acp, cwd="/workspace")
agent = Agent(model)

result = await agent.run("Summarize the current workspace state.")
print(result.output)
```

`acp_command` is for child processes that speak ACP JSON-RPC on stdin/stdout. It is not an arbitrary CLI wrapper.

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

`raise_on_empty_turn=True` converts a silent text turn into an ACP-specific
`UnexpectedModelBehavior` instead of returning an empty response. When
`session/new` reports `auth_required`, the provider calls `authenticate` with
the first advertised agent-managed method and retries session creation once.
Use `auth_method_id="..."` to select a specific method that has already been
prepared by the host. Environment-variable and terminal auth methods require
client-side credential or terminal setup and are not selected automatically.

For lower-level ownership, construct the provider directly:

```python
from pydantic_ai import Agent
from pydantic_acp import AcpProvider

# `remote_acp_agent` can be any object implementing the ACP Agent interface.
provider = AcpProvider(acp_agent=remote_acp_agent, cwd="/workspace")
session_id = await provider.ensure_session()
await provider.set_session_mode("review")
model = provider.model()
agent = Agent(model)

result = await agent.run("Summarize the current workspace state.")
print(result.output)
```

This keeps ownership boundaries explicit:

- Pydantic AI owns the outer agent run, output validation, and normal model/provider lifecycle.
- ACP owns the delegated agent session, ACP-visible updates, and any editor or host capabilities requested by that agent.
- `ensure_session()` initializes and creates the ACP session without consuming a prompt turn; `set_session_mode(...)` uses that same session.
- `create_acp_model(...)` and `provider.model()` leave ACP model selection to the wrapped agent's session default; pass `model_name="zed-agent"` or `provider.model("zed-agent")` only when the ACP agent exposes a selectable `"model"` `session/set_config_option` option.
- `AcpHostBridge` records ACP `session_update` messages and can delegate filesystem, terminal, approval, and extension callbacks to a real ACP host client when one is supplied.
- Pydantic AI function tools are intentionally not executed directly by `AcpModel`; register tools on the ACP agent or expose host capabilities through ACP.

Use this bridge when the thing you have is already an ACP agent and you want it to participate in code that expects a Pydantic AI provider/model. It is not another ACP server adapter and it does not replace `create_acp_agent(...)`.
If you are using Codex-backed Pydantic models through `codex-auth-helper`, pass explicit
instructions when building the model. That is the preferred seam for Codex-specific system behavior:

```python
from codex_auth_helper import create_codex_responses_model
from pydantic_ai import Agent

model = create_codex_responses_model(
    "gpt-5.4",
    instructions="You are a careful coding assistant.",
)
agent = Agent(model, name="codex-agent")
```

On the Pydantic path, `Agent(instructions=...)` is also valid and may still be useful for
agent-specific behavior layered on top of the model:

```python
from codex_auth_helper import create_codex_responses_model
from pydantic_ai import Agent

model = create_codex_responses_model(
    "gpt-5.4",
    instructions="You are a careful coding assistant.",
)
agent = Agent(
    model,
    name="codex-agent",
    instructions="Ask for clarification when the task is underspecified.",
)
```

In short: Codex-backed Pydantic models should not rely on an implicit default instruction string.
Set instructions explicitly at the factory level, and add `Agent(instructions=...)` when you want
extra agent-owned behavior.

## Native Plan Mode

`TaskPlan` is the structured native plan output surface.

Use `PrepareToolsBridge` to expose plan mode:

```python
from pydantic_ai import Agent
from pydantic_ai.tools import RunContext, ToolDefinition
from pydantic_acp import (
    AdapterConfig,
    PrepareToolsBridge,
    PrepareToolsMode,
    run_acp,
)


def read_only_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return list(tool_defs)


agent = Agent("openai:gpt-5", name="plan-agent")

run_acp(
    agent=agent,
    config=AdapterConfig(
        capability_bridges=[
            PrepareToolsBridge(
                default_mode_id="plan",
                default_plan_generation_type="structured",
                modes=[
                    PrepareToolsMode(
                        id="plan",
                        name="Plan",
                        description="Return a structured ACP task plan.",
                        prepare_func=read_only_tools,
                        plan_mode=True,
                    ),
                ],
            ),
        ],
    ),
)
```

Important behavior:

- `plan_generation_type="structured"` is the default plan-mode behavior
- `structured` mode expects structured `TaskPlan` output instead of exposing `acp_set_plan`
- switch to `plan_generation_type="tools"` when you explicitly want tool-based native plan recording
- keep `plan_tools=True` when you also want progress tools such as `acp_update_plan_entry`

## Projection Maps

Projection maps decide how known tool families render into ACP-visible updates.

Built-in projection helpers:

- `FileSystemProjectionMap`
- `HookProjectionMap`
- `WebToolProjectionMap`
- `BuiltinToolProjectionMap`

Example:

```python
from pydantic_acp import (
    AdapterConfig,
    BuiltinToolProjectionMap,
    FileSystemProjectionMap,
    HookProjectionMap,
    run_acp,
)

run_acp(
    agent=agent,
    config=AdapterConfig(
        projection_maps=[
            FileSystemProjectionMap(
                default_read_tool="read_file",
                default_write_tool="write_file",
            ),
            HookProjectionMap(
                hidden_event_ids=frozenset({"after_model_request"}),
                event_labels={"before_model_request": "Preparing Request"},
            ),
            BuiltinToolProjectionMap(),
        ],
    ),
)
```

## Capability Bridges

Current built-in bridges include:

- `ThinkingBridge`
- `PrepareToolsBridge`
- `ThreadExecutorBridge`
- `SetToolMetadataBridge`
- `IncludeToolReturnSchemasBridge`
- `WebSearchBridge`
- `WebFetchBridge`
- `ImageGenerationBridge`
- `McpCapabilityBridge`
- `SessionMcpBridge`
- `ToolsetBridge`
- `PrefixToolsBridge`
- `OpenAICompactionBridge`
- `AnthropicCompactionBridge`

Use bridges when the runtime should gain upstream Pydantic AI capabilities and ACP-visible metadata without rewriting the adapter core.

## Harness-backed Capabilities

`pydantic-acp` also ships a maintained bridge and projection layer for `pydantic-ai-harness`.

Public seams:

- `HarnessFileSystemBridge`
- `HarnessShellBridge`
- `HarnessCodeModeBridge`
- `HarnessFileSystemProjectionMap`
- `HarnessShellProjectionMap`
- `HarnessCodeModeProjectionMap`

Minimal example:

```python
from pathlib import Path

from pydantic_ai import Agent
from pydantic_acp import (
    AdapterConfig,
    HarnessFileSystemBridge,
    HarnessFileSystemProjectionMap,
    HarnessShellBridge,
    HarnessShellProjectionMap,
    MemorySessionStore,
    run_acp,
)

workspace_root = Path("agent_demos/harness-agent")

agent = Agent(
    "openai:gpt-5",
    name="harness-agent",
    instructions="Use the harness filesystem and shell tools inside the workspace only.",
)

run_acp(
    agent=agent,
    config=AdapterConfig(
        session_store=MemorySessionStore(),
        capability_bridges=[
            HarnessFileSystemBridge(root_dir=workspace_root),
            HarnessShellBridge(cwd=workspace_root),
        ],
        projection_maps=[
            HarnessFileSystemProjectionMap(),
            HarnessShellProjectionMap(),
        ],
    ),
)
```

Use `HarnessCodeModeBridge` only when the run should expose CodeMode. The maintained example keeps
that bridge opt-in so the native ACP target stays limited to filesystem and shell by default:

- [example source](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/mock_harness_agent.py)
- [detailed guide](https://github.com/vcoderun/acpkit/blob/main/docs/pydantic-acp/harness-capabilities.md)

The harness filesystem projection now renders `read_file` as a read-specific preview instead of a
fake diff, which makes ACP transcript output much more truthful for inspection-only tool calls.

## Factories, Sources, And Host-owned State

Use `agent_factory=` when the ACP session should influence which agent gets built:

```python
from pydantic_ai import Agent
from pydantic_acp import AcpSessionContext, AdapterConfig, MemorySessionStore, run_acp


def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    workspace_name = session.cwd.name
    model_name = "openai:gpt-5.4-mini"
    if workspace_name.endswith("-deep"):
        model_name = "openai:gpt-5.4"
    return Agent(model_name, name=f"workspace-{workspace_name}")


run_acp(
    agent_factory=build_agent,
    config=AdapterConfig(session_store=MemorySessionStore()),
)
```

Use `AgentSource` when the agent and its dependencies should be built separately. Use providers when models, modes, config values, plans, or approvals belong to the host layer instead of the adapter.

## Session Store Notes

Use `MemorySessionStore` for ephemeral local runs and `FileSessionStore` when ACP sessions should
survive process restarts. `FileSessionStore` is a local durable store, not a distributed coordination
layer.

File-backed session ids are constrained before they become filenames:

- allowed characters are ASCII letters, digits, `_`, and `-`
- maximum length is 128 characters
- path separators, dot-prefixed ids, whitespace, and shell metacharacters are rejected

The file store writes through a temp file, `fsync`, and atomic replace. Malformed or partially
written session files are skipped by public load/list flows.

## Maintained Examples

Maintained runnable examples:

- [finance_agent.py](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/finance_agent.py)
- [travel_agent.py](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/travel_agent.py)

Focused docs recipes:

- [Dynamic Factory Agents](https://vcoderun.github.io/acpkit/examples/dynamic-factory/)

## Documentation

- [Pydantic ACP Overview](https://vcoderun.github.io/acpkit/pydantic-acp/)
- [AdapterConfig](https://vcoderun.github.io/acpkit/pydantic-acp/adapter-config/)
- [Plans, Thinking, and Approvals](https://vcoderun.github.io/acpkit/pydantic-acp/plans-thinking-approvals/)
- [Models, Modes, and Slash Commands](https://vcoderun.github.io/acpkit/pydantic-acp/runtime-controls/)
- [Prompt Resources and Context](https://vcoderun.github.io/acpkit/pydantic-acp/prompt-resources/)
- [Session State and Lifecycle](https://vcoderun.github.io/acpkit/pydantic-acp/session-state/)
- [Bridges](https://vcoderun.github.io/acpkit/bridges/)
- [Providers](https://vcoderun.github.io/acpkit/providers/)
- [Security Guidance](https://vcoderun.github.io/acpkit/security/)
- [Host Backends and Projections](https://vcoderun.github.io/acpkit/host-backends/)
- [API Reference](https://vcoderun.github.io/acpkit/api/pydantic_acp/)

## Compatibility Policy

`pydantic-acp` supports `pydantic-ai-slim>=2.9.0,<=2.16.0`. Pydantic AI V1 and
Pydantic AI 2.x releases before 2.9.0 are outside the supported range.

The ACP client provider bridge depends on the Pydantic AI v2 `Provider` and `Model` contracts. Upgrades across major Pydantic AI versions should be deliberate because the adapter exposes both server-side ACP translation and client-side ACP provider integration.

Every supported minor is exercised by the repository's runtime and type-check
compatibility matrix. The adapter keeps upstream compatibility behind ACP Kit's
bridge and runtime seams instead of scattering version checks through callers.

Pydantic AI V2 defaults agent dependency and output generics to `object`. When
your tools or hooks explicitly use `RunContext[None]` or `Hooks[None]`, also
declare the dependency type on the agent:

```python
from pydantic_ai import Agent

agent: Agent[None, str] = Agent(
    "openai:gpt-5",
    deps_type=type(None),
    name="typed-agent",
)
```

The supported surface includes tool and output-tool preparation, output
validation and processing hooks, deferred tool-call hooks, run metadata,
conversation IDs, and the `run_stream_events()` lifecycle used through 2.16.0.

Harness-backed filesystem, shell, and CodeMode bridges are validated against
`pydantic-ai-harness[code-mode]==0.10.0` using its public capability imports.
Harness 0.10.0 requires `pydantic-ai-slim>=2.14.1`; the core adapter itself
remains compatible with Pydantic AI 2.9.0 through 2.16.0.
