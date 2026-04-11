# ACP Kit

ACP Kit is the adapter toolkit and monorepo for turning an existing agent surface into a truthful ACP server.

- `acpkit` is the root CLI and target resolver
- `pydantic-acp` is today's production-grade ACP adapter for `pydantic_ai.Agent`
- `codex-auth-helper` turns a local Codex login into a `pydantic-ai` Responses model

Today the stable production focus is `pydantic-acp`.

Additional adapters such as `langchain-acp` and `dspy-acp` are planned after `pydantic-acp`
reaches 1.0 stability.

ACP Kit is not a new agent framework. The core use case is:

1. keep your current agent surface
2. expose it through ACP without rewriting the agent
3. only publish models, modes, plans, approvals, MCP metadata, and host tools that the runtime can actually honor

The core workflow is simple:

1. build a normal `pydantic_ai.Agent`
2. expose it through ACP with `run_acp(...)` or `create_acp_agent(...)`
3. optionally add session stores, approvals, providers, projection maps, and bridges

Operational notes:

- `FileSessionStore(root=...)` is the recommended durable local store for editor and single-host ACP use; it uses atomic replace writes, local locking, and skips malformed saved sessions in public load/list flows
- dynamic mode slash commands come from configured mode ids; ids like `model`, `thinking`, `tools`, `hooks`, and `mcp-servers` are reserved
- custom `run_event_stream` hooks must return an async iterable, not a coroutine or plain value

## Installation

```bash
# production
uv pip install "acpkit[pydantic]"

# production with acpkit launch support
uv pip install "acpkit[pydantic,launch]"

# development
uv pip install -e ".[dev,docs,pydantic]"
```

## ACP Kit Skill

This repo also ships an `acpkit-sdk` skill package for Codex.

Use it when you want Codex to help integrate ACP into an existing agent surface, especially for:

- exposing an existing `pydantic_ai.Agent` through ACP
- choosing between `run_acp(...)`, `create_acp_agent(...)`, providers, bridges, and `AgentSource`
- wiring plans, approvals, session stores, thinking, MCP metadata, and host-backed tools
- keeping docs and examples aligned with the real SDK surface

From a checkout of this repo, install the skill with Unix commands:

```bash
mkdir -p "$HOME/.codex/skills/acpkit-sdk" \
  && cp -R .agents/skills/acpkit-sdk/. "$HOME/.codex/skills/acpkit-sdk/"
```

The canonical skill package lives here:

- [`.agents/skills/acpkit-sdk/`](https://github.com/vcoderun/acpkit/tree/main/.agents/skills/acpkit-sdk): packaged ACP Kit SDK skill for Codex

Example prompts:

- `Use $acpkit-sdk to expose my existing pydantic_ai.Agent through ACP.`
- `Use $acpkit-sdk to add ACP plans, approvals, and slash-command mode switching to this agent.`

## CLI

Run a supported agent target through ACP:

```bash
acpkit run strong_agent
acpkit run strong_agent:agent
acpkit run strong_agent:agent -p ./examples
```

`acpkit` resolves `module` or `module:attribute` targets, auto-detects `pydantic_ai.Agent`
instances, and dispatches them to the installed adapter package. If only the module is given, it
selects the last defined `pydantic_ai.Agent` instance in that module.

If the matching adapter extra is not installed, `acpkit` fails with an install hint such as
`uv pip install "acpkit[pydantic]"`.

Launch a target through Toad ACP:

```bash
acpkit launch strong_agent
acpkit launch strong_agent:agent -p ./examples
```

`acpkit launch TARGET` mirrors the resolved target to:

```bash
toad acp "acpkit run TARGET [-p PATH]..."
```

The command is dispatched through `uvx --python 3.14 --from batrachian-toad`, so Toad runs in a
separate Python 3.14 tool environment and does not replace your project Python.

If the script already starts its own ACP server and should be launched directly, use `--command`:

```bash
acpkit launch -c "python3.11 strong_agent.py"
```

`launch TARGET` and `launch --command ...` are mutually exclusive. `-p/--path` only applies to
`TARGET` mode.

## run_acp

Use `run_acp(...)` when you want to start an ACP server directly from a Pydantic AI agent:

```python
from pydantic_ai import Agent
from pydantic_acp import run_acp

agent = Agent("openai:gpt-5", name="weather-agent")

@agent.tool_plain
def get_weather(city: str) -> str:
    return f"Weather in {city}: sunny"

run_acp(agent=agent)
```

## create_acp_agent

Use `create_acp_agent(...)` when you want the ACP agent object without starting the server
immediately:

```python
import asyncio

from acp import run_agent
from pydantic_ai import Agent
from pydantic_acp import AdapterConfig, MemorySessionStore, create_acp_agent

agent = Agent("openai:gpt-5", name="composable-agent")

acp_agent = create_acp_agent(
    agent=agent,
    config=AdapterConfig(
        agent_name="my-service",
        agent_title="My Service Agent",
        session_store=MemorySessionStore(),
    ),
)

asyncio.run(run_agent(acp_agent))
```

## AdapterConfig

`AdapterConfig` is the main runtime configuration surface. Common fields include:

- agent metadata: `agent_name`, `agent_title`, `agent_version`
- persistence: `session_store`
- model selection: `allow_model_selection`, `available_models`, `models_provider`
- mode and config state: `modes_provider`, `config_options_provider`
- plans: `plan_provider`, or native plan state via `PrepareToolsMode(plan_mode=True)`
- approvals: `approval_bridge`, `approval_state_provider`
- bridges: `capability_bridges`
- projection and classification: `projection_maps`, `tool_classifier`, `enable_generic_tool_projection`

Configured adapter example:

```python
from pathlib import Path

from pydantic_ai import Agent
from pydantic_acp import (
    AdapterConfig,
    AdapterModel,
    FileSessionStore,
    NativeApprovalBridge,
    run_acp,
)

agent = Agent("openai:gpt-5", name="configured-agent")

config = AdapterConfig(
    agent_name="configured-agent",
    agent_title="Configured Agent",
    allow_model_selection=True,
    available_models=[
        AdapterModel(
            model_id="fast",
            name="Fast",
            description="Lower-latency responses",
            override="openai:gpt-5-mini",
        ),
        AdapterModel(
            model_id="smart",
            name="Smart",
            description="Higher-quality responses",
            override="openai:gpt-5",
        ),
    ],
    session_store=FileSessionStore(root=Path(".acp-sessions")),
    approval_bridge=NativeApprovalBridge(enable_persistent_choices=True),
)

run_acp(agent=agent, config=config)
```

## Native Plan State

When `plan_provider` is not configured, the adapter can manage ACP plan state natively. Enable it
by marking one `PrepareToolsMode` with `plan_mode=True` inside a `PrepareToolsBridge`:

```python
from pydantic_ai import Agent
from pydantic_ai.tools import RunContext, ToolDefinition
from pydantic_acp import AdapterConfig, PrepareToolsBridge, PrepareToolsMode, run_acp


def plan_tools(
    ctx: RunContext[None], tool_defs: list[ToolDefinition]
) -> list[ToolDefinition]:
    del ctx
    return list(tool_defs)


def agent_tools(
    ctx: RunContext[None], tool_defs: list[ToolDefinition]
) -> list[ToolDefinition]:
    del ctx
    return list(tool_defs)


agent = Agent("openai:gpt-5", name="plan-agent")

run_acp(
    agent=agent,
    config=AdapterConfig(
        capability_bridges=[
            PrepareToolsBridge(
                default_mode_id="agent",
                modes=[
                    PrepareToolsMode(
                        id="plan",
                        name="Plan",
                        description="Inspect and write plans.",
                        prepare_func=plan_tools,
                        plan_mode=True,
                    ),
                    PrepareToolsMode(
                        id="agent",
                        name="Agent",
                        description="Full tool surface.",
                        prepare_func=agent_tools,
                    ),
                ],
            ),
        ],
    ),
)
```

When the session is in `plan` mode, the adapter:

- injects `acp_get_plan` and `acp_set_plan` as hidden tools on the agent
- extends `output_type` with `NativePlanGeneration` so the agent can emit a structured plan in a
  single response

`NativePlanGeneration` fields:
- `plan_entries: list[PlanEntry]` — structured plan entries
- `plan_md: str` — optional markdown representation

Native plan state and `plan_provider` are mutually exclusive. See
[Providers](https://vcoderun.github.io/acpkit/providers/) for full
details.

## Agent Factories

Use a factory or custom `AgentSource` when agent construction depends on the current session:

```python
from pydantic_ai import Agent
from pydantic_acp import AcpSessionContext, create_acp_agent

def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    return Agent(
        "openai:gpt-5",
        name=f"agent-{session.cwd.name}",
        system_prompt=f"Work inside {session.cwd.name}.",
    )

acp_agent = create_acp_agent(agent_factory=build_agent)
```

`StaticAgentSource` accepts an optional `deps` field to pass typed runtime dependencies alongside
a shared agent instance without a factory:

```python
from pydantic_acp import AdapterConfig, create_acp_agent
from pydantic_acp.agent_source import StaticAgentSource
from pydantic_ai import Agent

from myapp.deps import AppDependencies

deps = AppDependencies(db=my_db, cache=my_cache)
agent = Agent("openai:gpt-5", name="deps-agent", deps_type=AppDependencies)

acp_agent = create_acp_agent(
    agent_source=StaticAgentSource(agent=agent, deps=deps),
)
```

## Session Stores

`MemorySessionStore` is the default. Use `FileSessionStore` when sessions should survive process
restarts:

```python
from pathlib import Path

from pydantic_ai import Agent
from pydantic_acp import AdapterConfig, FileSessionStore, run_acp

agent = Agent("openai:gpt-5", name="persistent-agent")

run_acp(
    agent=agent,
    config=AdapterConfig(
        session_store=FileSessionStore(root=Path(".acp-sessions")),
    ),
)
```

Session lifecycle support includes create, load, list, fork, resume, close, transcript replay, and
message-history replay.

## Runtime Controls

The adapter exposes a small ACP control plane alongside normal prompts:

- `/model`
  print the current session model id
- `/model provider:model`
  switch the current session model
- `/tools`
  list registered tools
- `/hooks`
  list registered `Hooks` callbacks visible on the current agent
- `/mcp-servers`
  list MCP servers extracted from the current agent toolsets and session metadata

Codex-backed model changes must be explicit:

```text
/model codex:gpt-5.4
```

## Approval Flow

Pydantic AI approval-gated tools are bridged to ACP permission requests:

```python
from pydantic_ai import Agent
from pydantic_ai.exceptions import ApprovalRequired
from pydantic_ai.tools import RunContext
from pydantic_acp import AdapterConfig, NativeApprovalBridge, run_acp

agent = Agent("openai:gpt-5", name="approval-agent")

@agent.tool
def delete_file(ctx: RunContext[None], path: str) -> str:
    if not ctx.tool_call_approved:
        raise ApprovalRequired()
    return f"Deleted {path}"

run_acp(
    agent=agent,
    config=AdapterConfig(
        approval_bridge=NativeApprovalBridge(enable_persistent_choices=True),
    ),
)
```

## Projection Maps

`projection_maps` lets known tool families render as richer ACP content instead of raw text.

### FileSystemProjectionMap

`FileSystemProjectionMap` can project:

- read tools into ACP diff previews
- write tools into ACP file diffs
- bash tools into command previews and terminal references

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

### HookProjectionMap

`HookProjectionMap` controls how existing `pydantic_ai.capabilities.Hooks` callbacks render into
ACP updates:

```python
from pydantic_ai import Agent
from pydantic_ai.capabilities import Hooks
from pydantic_acp import HookProjectionMap, run_acp

hooks = Hooks[None]()

@hooks.on.before_model_request
async def log_request(ctx, request_context):
    del ctx
    return request_context

agent = Agent("openai:gpt-5", capabilities=[hooks])

run_acp(
    agent=agent,
    projection_maps=(
        HookProjectionMap(
            hidden_event_ids=frozenset({"after_model_request"}),
            event_labels={"before_model_request": "Preparing Request"},
        ),
    ),
)
```

## Capability Bridges

Capability bridges extend ACP exposure without coupling the adapter core to one product runtime.
Built-in bridges cover:

- `HookBridge`
- `PrepareToolsBridge`
- `HistoryProcessorBridge`
- `McpBridge`
- `AgentBridgeBuilder`

See [Bridges](https://vcoderun.github.io/acpkit/bridges/) for the full bridge model.

## Providers

Providers let the host own session state while the adapter exposes it through ACP:

- `SessionModelsProvider`
- `SessionModesProvider`
- `ConfigOptionsProvider`
- `PlanProvider`
- `ApprovalStateProvider`

See [Providers](https://vcoderun.github.io/acpkit/providers/) for full details.

## Host Backends

`ClientHostContext` provides session-scoped access to ACP client-backed filesystem and terminal
operations:

```python
from acp.interfaces import Client as AcpClient
from pydantic_ai import Agent
from pydantic_acp import AcpSessionContext, ClientHostContext

def build_agent(client: AcpClient, session: AcpSessionContext) -> Agent[None, str]:
    host = ClientHostContext.from_session(client=client, session=session)
    agent = Agent("openai:gpt-5")

    @agent.tool
    async def read_user_file(ctx, path: str) -> str:
        del ctx
        result = await host.filesystem.read_text_file(path)
        return result.content

    return agent
```

See [Host Backends And Projections](https://vcoderun.github.io/acpkit/host-backends/) for the
filesystem and terminal API surface.

## Codex Auth Helper

`codex-auth-helper` reads `~/.codex/auth.json`, refreshes tokens when needed, builds a Codex-aware
`AsyncOpenAI` client, and returns a ready-to-use `OpenAIResponsesModel`.

```python
from pydantic_ai import Agent
from codex_auth_helper import create_codex_responses_model

agent = Agent(create_codex_responses_model("gpt-5.4"))
```

See [Helpers](https://vcoderun.github.io/acpkit/helpers/) for helper package details.

## Examples

Runnable and focused examples live under [examples/pydantic](https://github.com/vcoderun/acpkit/tree/main/examples/pydantic):

- [static_agent.py](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/static_agent.py)
  smallest direct `run_acp(agent=...)` setup
- [factory_agent.py](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/factory_agent.py)
  session-aware factory plus session-local model selection
- [providers.py](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/providers.py)
  models, modes, config options, plan updates, and approval metadata
- [bridges.py](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/bridges.py)
  bridge builder, prepare-tools, history processors, and MCP metadata
- [approvals.py](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/approvals.py)
  native deferred approval flow
- [host_context.py](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/host_context.py)
  `ClientHostContext` usage inside a factory-built agent
- [hook_projection.py](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/hook_projection.py)
  existing `Hooks` capability introspection rendered through `HookProjectionMap`
- [strong_agent.py](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/strong_agent.py)
  full-featured workspace agent example combining factories, providers, approvals, bridges, projection maps, `ask/plan/agent` modes, and host helpers

## Development

Canonical local checks:

```bash
make tests
make check
make save-coverage
```

Preview the docs locally:

```bash
make serve
```

## Documentation Map

- [ACP Kit](https://vcoderun.github.io/acpkit/): landing page and package overview
- [Installation](https://vcoderun.github.io/acpkit/getting-started/installation/): install paths and validation workflow
- [Quickstart](https://vcoderun.github.io/acpkit/getting-started/quickstart/): first ACP server in a few steps
- [CLI](https://vcoderun.github.io/acpkit/cli/): root `acpkit` CLI behavior
- [Pydantic ACP Overview](https://vcoderun.github.io/acpkit/pydantic-acp/): adapter architecture and entry points
- [AdapterConfig](https://vcoderun.github.io/acpkit/pydantic-acp/adapter-config/): full `AdapterConfig` guide
- [Models, Modes, and Slash Commands](https://vcoderun.github.io/acpkit/pydantic-acp/runtime-controls/): models, modes, slash commands, and thinking
- [Plans, Thinking, and Approvals](https://vcoderun.github.io/acpkit/pydantic-acp/plans-thinking-approvals/): ACP planning, reasoning effort, and approval flow
- [Providers](https://vcoderun.github.io/acpkit/providers/): provider seams and host-owned state
- [Bridges](https://vcoderun.github.io/acpkit/bridges/): capability bridges and bridge builder usage
- [Host Backends And Projections](https://vcoderun.github.io/acpkit/host-backends/): client filesystem, terminal helpers, and projections
- [Helpers](https://vcoderun.github.io/acpkit/helpers/): helper packages including `codex-auth-helper`
- [Workspace Agent](https://vcoderun.github.io/acpkit/examples/workspace-agent/): full coding-agent walkthrough
- [Testing](https://vcoderun.github.io/acpkit/testing/): behavioral test surface and validation commands
- [examples/pydantic/README.md](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/README.md): runnable demos and focused SDK examples

## License

Apache 2.0
