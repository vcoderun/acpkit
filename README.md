# ACP Kit

ACP Kit is a monorepo for ACP-facing agent runtime packages.

- `acpkit` is the root CLI and target resolver
- `pydantic-acp` adapts `pydantic_ai.Agent` instances to ACP
- `codex-auth-helper` turns a local Codex login into a `pydantic-ai` Responses model

The core workflow is simple:

1. build a normal `pydantic_ai.Agent`
2. expose it through ACP with `run_acp(...)` or `create_acp_agent(...)`
3. optionally add session stores, approvals, providers, projection maps, and bridges

## Installation

Production:

```bash
uv pip install "acpkit[pydantic]"
```

```bash
pip install "acpkit[pydantic]"
```

Development:

```bash
uv sync --extra dev --extra docs --extra pydantic
```

```bash
pip install -e ".[dev,docs,pydantic]"
```

## CLI

Run a supported agent target through ACP:

```bash
acpkit run my_agent
acpkit run my_agent:agent
acpkit run my_agent:agent -p ./examples
```

`acpkit` resolves `module` or `module:attribute` targets, auto-detects `pydantic_ai.Agent`
instances, and dispatches them to the installed adapter package. If only the module is given, it
selects the last defined `pydantic_ai.Agent` instance in that module.

If the matching adapter extra is not installed, `acpkit` fails with an install hint such as
`uv pip install "acpkit[pydantic]"`.

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
- plans: `plan_provider`
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
    session_store=FileSessionStore(base_dir=Path(".acp-sessions")),
    approval_bridge=NativeApprovalBridge(enable_persistent_choices=True),
)

run_acp(agent=agent, config=config)
```

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
        session_store=FileSessionStore(base_dir=Path(".acp-sessions")),
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
/model codex:gpt-5
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

See [docs/bridges.md](/Users/mert/Desktop/acpkit/docs/bridges.md) for the full bridge model.

## Providers

Providers let the host own session state while the adapter exposes it through ACP:

- `SessionModelsProvider`
- `SessionModesProvider`
- `ConfigOptionsProvider`
- `PlanProvider`
- `ApprovalStateProvider`

See [docs/providers.md](/Users/mert/Desktop/acpkit/docs/providers.md) for full details.

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

See [docs/host-backends.md](/Users/mert/Desktop/acpkit/docs/host-backends.md) for the filesystem
and terminal API surface.

## Codex Auth Helper

`codex-auth-helper` reads `~/.codex/auth.json`, refreshes tokens when needed, builds a Codex-aware
`AsyncOpenAI` client, and returns a ready-to-use `OpenAIResponsesModel`.

```python
from pydantic_ai import Agent
from codex_auth_helper import create_codex_responses_model

agent = Agent(create_codex_responses_model("gpt-5"))
```

See [docs/helpers.md](/Users/mert/Desktop/acpkit/docs/helpers.md) for helper package details.

## Examples

Runnable and focused examples live under [examples/pydantic](/Users/mert/Desktop/acpkit/examples/pydantic):

- [static_agent.py](/Users/mert/Desktop/acpkit/examples/pydantic/static_agent.py)
  smallest direct `run_acp(agent=...)` setup
- [factory_agent.py](/Users/mert/Desktop/acpkit/examples/pydantic/factory_agent.py)
  session-aware factory plus session-local model selection
- [providers.py](/Users/mert/Desktop/acpkit/examples/pydantic/providers.py)
  models, modes, config options, plan updates, and approval metadata
- [bridges.py](/Users/mert/Desktop/acpkit/examples/pydantic/bridges.py)
  bridge builder, prepare-tools, history processors, and MCP metadata
- [approvals.py](/Users/mert/Desktop/acpkit/examples/pydantic/approvals.py)
  native deferred approval flow
- [host_context.py](/Users/mert/Desktop/acpkit/examples/pydantic/host_context.py)
  `ClientHostContext` usage inside a factory-built agent
- [hook_projection.py](/Users/mert/Desktop/acpkit/examples/pydantic/hook_projection.py)
  existing `Hooks` capability introspection rendered through `HookProjectionMap`
- [my_agent.py](/Users/mert/Desktop/acpkit/examples/pydantic/my_agent.py)
  broad end-to-end demo combining factories, providers, approvals, bridges, projection maps, and host helpers

## Development

Canonical local checks:

```bash
uv run ruff check
uv run ty check
uv run basedpyright
make tests
make check
```

Preview the docs locally:

```bash
make serve
```

## Documentation Map

- [docs/index.md](/Users/mert/Desktop/acpkit/docs/index.md): workspace overview and package map
- [docs/cli.md](/Users/mert/Desktop/acpkit/docs/cli.md): root `acpkit` CLI behavior
- [docs/pydantic-acp.md](/Users/mert/Desktop/acpkit/docs/pydantic-acp.md): adapter API, runtime controls, approvals, projection maps, providers, and host backends
- [docs/bridges.md](/Users/mert/Desktop/acpkit/docs/bridges.md): capability bridges and hook rendering
- [docs/providers.md](/Users/mert/Desktop/acpkit/docs/providers.md): provider seams and host-owned state
- [docs/host-backends.md](/Users/mert/Desktop/acpkit/docs/host-backends.md): client filesystem and terminal helpers
- [docs/helpers.md](/Users/mert/Desktop/acpkit/docs/helpers.md): helper packages including `codex-auth-helper`
- [docs/testing.md](/Users/mert/Desktop/acpkit/docs/testing.md): behavioral test surface and validation commands
- [examples/pydantic/README.md](/Users/mert/Desktop/acpkit/examples/pydantic/README.md): runnable demos and focused SDK examples

## License

Apache 2.0
