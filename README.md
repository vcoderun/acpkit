# ACP Kit

[![CI](https://github.com/vcoderun/acpkit/actions/workflows/ci.yml/badge.svg?event=push)](https://github.com/vcoderun/acpkit/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/vcoderun/acpkit/branch/main/graph/badge.svg?token=ZQL4NY4FK6)](https://codecov.io/gh/vcoderun/acpkit) [![PyPI version](https://img.shields.io/pypi/v/acpkit.svg)](https://pypi.org/project/acpkit/) [![Python versions](https://img.shields.io/pypi/pyversions/acpkit.svg)](https://pypi.org/project/acpkit/) [![GitHub release](https://img.shields.io/github/v/release/vcoderun/acpkit)](https://github.com/vcoderun/acpkit/releases) [![License](https://img.shields.io/github/license/vcoderun/acpkit)](https://github.com/vcoderun/acpkit/blob/main/LICENSE)

ACP Kit is the adapter toolkit and monorepo for exposing existing agent runtimes through ACP without inventing runtime features that are not really there.

Today the repo ships two production-grade adapter families:

- `pydantic-acp`
- `langchain-acp`

Supporting packages sit alongside those adapters:

- `acpkit`
- `codex-auth-helper`
- `acpremote`

Package map:

- `pydantic-acp`
  Production-grade ACP adapter for `pydantic_ai.Agent`.
- `langchain-acp`
  Production-grade ACP adapter for LangChain, LangGraph, and DeepAgents compiled graphs.
- `acpkit`
  Root CLI, target resolver, and launch helpers.
- `codex-auth-helper`
  Helper package for building Codex-backed Pydantic AI Responses models from a local Codex login.
- `acpremote`
  Generic WebSocket transport helper for exposing any ACP agent remotely and mirroring a remote ACP server back into a local ACP boundary.

ACP Kit is not a new agent framework. The intended workflow is:

1. Keep your existing agent surface.
2. Expose it through ACP with `run_acp(...)` or `create_acp_agent(...)`.
3. Add only the ACP-visible state your runtime can actually honor: models, modes, plans, approvals, projection maps, MCP metadata, and host-backed tools.

## Installation

Production:

```bash
uv add "acpkit[pydantic]"
```

```bash
pip install "acpkit[pydantic]"
```

LangChain and LangGraph support:

```bash
uv add "acpkit[langchain]"
```

```bash
pip install "acpkit[langchain]"
```

DeepAgents compatibility on top of `langchain-acp`:

```bash
uv add "acpkit[deepagents]"
```

```bash
pip install "acpkit[deepagents]"
```

Remote transport helpers:

```bash
uv add "acpkit[remote]"
```

```bash
pip install "acpkit[remote]"
```

With `acpkit launch` support:

```bash
uv add "acpkit[pydantic,launch]"
```

```bash
pip install "acpkit[pydantic,launch]"
```

Contributor setup:

```bash
uv sync --extra dev --extra docs --extra pydantic --extra langchain
```

```bash
pip install -e ".[dev,docs,pydantic,langchain]"
```

Contributor setup and validation commands are documented in [CONTRIBUTING.md](https://github.com/vcoderun/acpkit/blob/main/CONTRIBUTING.md).

## Quickstart

ACP Kit has two primary adapter entry points. Pick the one that matches the runtime you already have.

Pydantic path:

```python
from pydantic_ai import Agent
from pydantic_acp import run_acp

agent = Agent("openai:gpt-5", name="weather-agent")


@agent.tool_plain
def get_weather(city: str) -> str:
    return f"Weather in {city}: sunny"


run_acp(agent=agent)
```

LangChain or LangGraph path:

```python
from langchain.agents import create_agent
from langchain_acp import run_acp

graph = create_agent(model="openai:gpt-5", tools=[])

run_acp(graph=graph)
```

If ACP session state should influence what gets built, both adapters expose a factory seam:

- `pydantic-acp`: `agent_factory=session -> Agent`
- `langchain-acp`: `graph_factory=session -> CompiledStateGraph`

`acpremote` is not an adapter. It is a transport/helper package for exposing or consuming any existing `acp.interfaces.Agent`.

Docs:

- Overview: <https://vcoderun.github.io/acpkit/>
- Installation: <https://vcoderun.github.io/acpkit/getting-started/installation/>
- Quickstart hub: <https://vcoderun.github.io/acpkit/getting-started/quickstart/>
- Pydantic quickstart: <https://vcoderun.github.io/acpkit/getting-started/pydantic-quickstart/>
- LangChain quickstart: <https://vcoderun.github.io/acpkit/getting-started/langchain-quickstart/>
- Pydantic ACP overview: <https://vcoderun.github.io/acpkit/pydantic-acp/>
- LangChain ACP overview: <https://vcoderun.github.io/acpkit/langchain-acp/>
- Helpers overview: <https://vcoderun.github.io/acpkit/helpers/>
- `acpremote` package: <https://vcoderun.github.io/acpkit/acpremote/>

## CLI

Expose a supported target through ACP:

```bash
acpkit run my_agent
acpkit run my_agent:agent
acpkit run app.agents.demo:agent -p ./examples
```

Mirror a remote ACP WebSocket endpoint back into a local stdio ACP server:

```bash
acpkit run --addr ws://127.0.0.1:8080/acp/ws
acpkit run --addr ws://agents.example.com/acp/ws --token-env ACPREMOTE_BEARER_TOKEN
```

`acpkit` resolves `module` or `module:attribute` targets, auto-detects supported runtime objects, and dispatches them to the installed adapter package. If only the module is given, it selects the last defined supported target instance in that module.

Launch a target through Toad ACP:

```bash
acpkit launch my_agent
acpkit launch my_agent:agent -p ./examples
```

If the script already starts its own ACP server and should be launched directly:

```bash
acpkit launch -c "python3.11 finance_agent.py"
```

`launch TARGET` and `launch --command ...` are mutually exclusive. `-p/--path` only applies to `TARGET` mode.

`acpkit run` also resolves module-level LangChain and DeepAgents graphs:

```bash
acpkit run examples.langchain.workspace_graph:graph
acpkit run examples.langchain.deepagents_graph:graph
```

If the module omits `:attribute`, `acpkit` selects the last defined supported target instance in that module, regardless of whether it is a Pydantic AI agent or a LangGraph graph.

Expose any supported target through the remote WebSocket transport:

```bash
acpkit serve examples.pydantic.finance_agent:agent
acpkit serve examples.langchain.workspace_graph:graph --host 0.0.0.0 --port 8080
```

If you already have a native ACP agent object, `acpkit run module:agent` can dispatch that directly too.

## What `acpremote` Supports

`acpremote` is transport-only. It does not require ACP Kit adapters on either side as long as an ACP agent already exists.

Core surfaces include:

- `serve_acp(...)` for exposing any `acp.interfaces.Agent` over WebSocket
- `serve_command(...)` for exposing any stdio ACP command over WebSocket
- `connect_acp(...)` for turning a remote ACP WebSocket endpoint back into a local ACP agent proxy
- `acpkit serve ...` for serving supported ACP Kit targets remotely
- `acpkit run --addr ...` for mirroring a remote ACP endpoint into a local stdio ACP server
- `/acp` metadata and `/healthz` HTTP routes alongside the WebSocket endpoint
- optional bearer-token protection for the WebSocket endpoint
- optional latency logging through `TransportOptions(emit_latency_meta=True, emit_latency_projection=True)`

`acpremote` examples:

- docs: <https://vcoderun.github.io/acpkit/acpremote/>
- docs: <https://vcoderun.github.io/acpkit/examples/remote-hosting/>
- source: <https://github.com/vcoderun/acpkit/blob/main/examples/acpremote/serve_pydantic_finance.py>
- source: <https://github.com/vcoderun/acpkit/blob/main/examples/acpremote/serve_langchain_workspace.py>
- source: <https://github.com/vcoderun/acpkit/blob/main/examples/acpremote/connect_mirror.py>
- source: <https://github.com/vcoderun/acpkit/blob/main/examples/acpremote/expose_codex.py>
- source: <https://github.com/vcoderun/acpkit/blob/main/examples/acpremote/connect_codex.py>
- guide: <https://github.com/vcoderun/acpkit/blob/main/examples/acpremote/README.md>

For the end-to-end remote flow, the common split is:

- remote host: `acpkit serve ...` or `acpremote.serve_command(...)`
- local client: `acpkit run --addr ...` or `acpremote.connect_acp(...)`
- launcher integration: `toad acp "acpkit run --addr ..."`

## What `pydantic-acp` Supports

`AdapterConfig` is the main runtime surface. Common ownership seams include:

- session stores and lifecycle
- model selection
- mode and config state
- approval bridges
- native plan state or host-owned plan providers
- capability bridges
- projection maps and tool classification
- prompt-model override providers

Prompt resource support includes:

- ACP text blocks
- resource links
- embedded text resources
- image blocks
- audio blocks
- embedded binary resources

Host-facing utilities include:

- `HostAccessPolicy` for typed filesystem and terminal guardrails
- `ClientHostContext` for ACP client-backed host access
- `BlackBoxHarness` for ACP boundary integration tests
- `CompatibilityManifest` for documenting the ACP surface an integration truly supports

## What `langchain-acp` Supports

`langchain-acp` keeps ACP Kit's adapter seams intact while staying graph-centric on the upstream side.

Core surfaces include:

- `graph`, `graph_factory`, and `graph_source`
- session stores and transcript replay
- model, mode, and config-option providers
- native ACP plan state with `TaskPlan`
- approval bridging from LangChain `HumanInTheLoopMiddleware`
- capability bridges and graph-build contributions
- projection maps and event projection maps
- DeepAgents compatibility through `DeepAgentsCompatibilityBridge` and `DeepAgentsProjectionMap`

Prompt and event handling covers:

- resource and multimodal prompt conversion for ACP inputs
- streamed text handling from LangChain and LangGraph events
- structured event projection when graph output should stay visible in ACP clients
- richer tool projection presets for filesystem, browser, HTTP, search, finance, and DeepAgents-style tool families

Maintained integration paths include:

- plain LangChain `create_agent(...)` graphs
- compiled LangGraph graphs
- DeepAgents graphs through the compatibility bridge
- session-aware `graph_factory(session)` builds when ACP session state should influence graph construction

That lets the adapter expose plain LangChain graphs, compiled LangGraph graphs, and DeepAgents graphs without collapsing everything into one bespoke runtime.

## Native Plan Mode And `TaskPlan`

`pydantic-acp` now uses `TaskPlan` as the structured native plan output surface.

Native plan mode is typically enabled through `PrepareToolsBridge`:

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

Key rules:

- `plan_generation_type="structured"` is the default native plan-mode behavior.
- In `structured` mode, the adapter expects structured `TaskPlan` output instead of exposing `acp_set_plan`.
- Switch to `plan_generation_type="tools"` when you explicitly want tool-based native plan recording.
- Keep `plan_tools=True` for progress tools such as `acp_update_plan_entry` and `acp_mark_plan_done`.
- Native plan state and a host-owned `plan_provider` are separate seams. Use one truth source per workflow.

## Projection Maps

Projection maps decide how known tool families render into ACP-visible updates instead of raw text blobs.

Built-in projection helpers:

- `FileSystemProjectionMap`
  Filesystem reads, writes, and command previews into ACP diffs and rich status cards.
- `HookProjectionMap`
  Re-label or hide selected `Hooks(...)` lifecycle events.
- `WebToolProjectionMap`
  Rich rendering for web-search and web-fetch style tool families.
- `BuiltinToolProjectionMap`
  Rich rendering for built-in upstream capability tools such as web search, web fetch, image generation, and upstream MCP capability calls.

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

Capability bridges extend runtime behavior without hard-coding one product shape into the adapter core.

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
- `ToolsetBridge`
- `PrefixToolsBridge`
- `OpenAICompactionBridge`
- `AnthropicCompactionBridge`

Use bridges when the runtime should gain upstream Pydantic AI capabilities and ACP-visible metadata without rewriting the adapter core.

## Maintained Examples

The maintained example set is intentionally small. Each example is broad enough to be useful on its own instead of only demonstrating one narrow helper.

- [Finance Agent](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/finance_agent.py)
  Session-aware finance workspace with ACP plans, approvals, mode-aware tool shaping, and projected note diffs.
- [Travel Agent](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/travel_agent.py)
  Travel planning runtime with hook projection, approval-gated trip files, and prompt-model override behavior for media prompts.
- [Workspace Graph](https://github.com/vcoderun/acpkit/blob/main/examples/langchain/workspace_graph.py)
  Plain LangChain graph wiring with a module-level `graph`, session-aware `graph_from_session(...)`, and filesystem read/write projection.
- [DeepAgents Graph](https://github.com/vcoderun/acpkit/blob/main/examples/langchain/deepagents_graph.py)
  DeepAgents compatibility wiring through `langchain-acp`, approvals, and projection presets.
- [ACP Remote Hosting](https://github.com/vcoderun/acpkit/blob/main/examples/acpremote/README.md)
  Adapter-backed remote hosting for both the maintained Pydantic and LangChain examples, plus direct ACP command transport.

Run them with:

```bash
uv run python -m examples.pydantic.finance_agent
uv run python -m examples.pydantic.travel_agent
uv run python -m examples.langchain.workspace_graph
uv run python -m examples.langchain.deepagents_graph
uv run python examples/acpremote/serve_pydantic_finance.py
uv run python examples/acpremote/serve_langchain_workspace.py
```

## Documentation Map

Top-level docs:

- [Getting Started](https://vcoderun.github.io/acpkit/getting-started/quickstart/)
- [CLI](https://vcoderun.github.io/acpkit/cli/)
- [Pydantic ACP Overview](https://vcoderun.github.io/acpkit/pydantic-acp/)
- [LangChain ACP Overview](https://vcoderun.github.io/acpkit/langchain-acp/)
- [Helpers](https://vcoderun.github.io/acpkit/helpers/)
- [acpremote Overview](https://vcoderun.github.io/acpkit/acpremote/)
- [AdapterConfig](https://vcoderun.github.io/acpkit/pydantic-acp/adapter-config/)
- [Plans, Thinking, and Approvals](https://vcoderun.github.io/acpkit/pydantic-acp/plans-thinking-approvals/)
- [Prompt Resources and Context](https://vcoderun.github.io/acpkit/pydantic-acp/prompt-resources/)
- [Session State and Lifecycle](https://vcoderun.github.io/acpkit/pydantic-acp/session-state/)
- [Models, Modes, and Slash Commands](https://vcoderun.github.io/acpkit/pydantic-acp/runtime-controls/)
- [Bridges](https://vcoderun.github.io/acpkit/bridges/)
- [Providers](https://vcoderun.github.io/acpkit/providers/)
- [Host Backends and Projections](https://vcoderun.github.io/acpkit/host-backends/)
- [Projection Cookbook](https://vcoderun.github.io/acpkit/projection-cookbook/)
- [Examples](https://vcoderun.github.io/acpkit/examples/)
- [LangChain Workspace Graph](https://vcoderun.github.io/acpkit/examples/langchain-workspace/)
- [DeepAgents Compatibility Example](https://vcoderun.github.io/acpkit/examples/deepagents/)
- [Remote ACP Hosting](https://vcoderun.github.io/acpkit/examples/remote-hosting/)
- [Dynamic Factory Agents](https://vcoderun.github.io/acpkit/examples/dynamic-factory/)
- [Testing](https://vcoderun.github.io/acpkit/testing/)

Reference docs:

- [`acpkit` API](https://vcoderun.github.io/acpkit/api/acpkit/)
- [`langchain_acp` API](https://vcoderun.github.io/acpkit/api/langchain_acp/)
- [`pydantic_acp` API](https://vcoderun.github.io/acpkit/api/pydantic_acp/)
- [`codex_auth_helper` API](https://vcoderun.github.io/acpkit/api/codex_auth_helper/)

## ACP Kit Skill

This repo also ships an `acpkit-sdk` skill package for Codex.

Use it when you want Codex to help integrate ACP into an existing agent surface, especially for:

- exposing an existing `pydantic_ai.Agent` through ACP
- choosing between `run_acp(...)`, `create_acp_agent(...)`, providers, bridges, and `AgentSource`
- wiring plans, approvals, session stores, thinking, MCP metadata, and host-backed tools
- keeping docs and examples aligned with the real SDK surface

Install with just one command:

```bash
npx ctx7 skills install /vcoderun/acpkit acpkit-sdk
```

Canonical skill package:

- [`.agents/skills/acpkit-sdk/`](https://github.com/vcoderun/acpkit/tree/main/.agents/skills/acpkit-sdk)

Example prompts:

- `Use $acpkit-sdk to expose my existing pydantic_ai.Agent through ACP.`
- `Use $acpkit-sdk to add ACP plans, approvals, slash-command mode switching, and projection maps to this agent.`
