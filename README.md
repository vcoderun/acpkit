# ACP Kit

[![CI](https://github.com/vcoderun/acpkit/actions/workflows/ci.yml/badge.svg?event=push)](https://github.com/vcoderun/acpkit/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/vcoderun/acpkit/branch/main/graph/badge.svg?token=ZQL4NY4FK6)](https://codecov.io/gh/vcoderun/acpkit) [![PyPI version](https://img.shields.io/pypi/v/acpkit.svg)](https://pypi.org/project/acpkit/) [![Python versions](https://img.shields.io/pypi/pyversions/acpkit.svg)](https://pypi.org/project/acpkit/) [![GitHub release](https://img.shields.io/github/v/release/vcoderun/acpkit)](https://github.com/vcoderun/acpkit/releases) [![License](https://img.shields.io/github/license/vcoderun/acpkit)](https://github.com/vcoderun/acpkit/blob/main/LICENSE)

ACP Kit is the adapter toolkit and monorepo for turning an existing agent surface into a truthful ACP server boundary.

Today the production focus is `pydantic-acp`: exposing an existing `pydantic_ai.Agent` through ACP without rewriting the agent or inventing ACP state the runtime cannot really honor.

The repo currently ships three main Python packages:

- `acpkit`
  Root CLI, target resolver, and launch helpers.
- `pydantic-acp`
  Production-grade ACP adapter for `pydantic_ai.Agent`.
- `codex-auth-helper`
  Helper package for building Codex-backed Pydantic AI Responses models from a local Codex login.

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

With `acpkit launch` support:

```bash
uv add "acpkit[pydantic,launch]"
```

```bash
pip install "acpkit[pydantic,launch]"
```

Contributor setup:

```bash
uv sync --extra dev --extra docs --extra pydantic
```

```bash
pip install -e ".[dev,docs,pydantic]"
```

Contributor setup and validation commands are documented in [CONTRIBUTING.md](https://github.com/vcoderun/acpkit/blob/main/CONTRIBUTING.md).

## Quickstart

```python
from pydantic_ai import Agent
from pydantic_acp import run_acp

agent = Agent("openai:gpt-5", name="weather-agent")


@agent.tool_plain
def get_weather(city: str) -> str:
    return f"Weather in {city}: sunny"


run_acp(agent=agent)
```

If you want the ACP agent object without starting the server immediately:

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

If the ACP session should influence which agent gets built, use `agent_factory=`:

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

Use this seam when the session metadata, config values, or workspace path should change the agent instance dynamically.

## CLI

Expose a supported target through ACP:

```bash
acpkit run my_agent
acpkit run my_agent:agent
acpkit run app.agents.demo:agent -p ./examples
```

`acpkit` resolves `module` or `module:attribute` targets, auto-detects supported agent instances, and dispatches them to the installed adapter package. If only the module is given, it selects the last defined supported agent instance in that module.

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

Run them with:

```bash
uv run python -m examples.pydantic.finance_agent
uv run python -m examples.pydantic.travel_agent
```

## Documentation Map

Top-level docs:

- [Getting Started](https://vcoderun.github.io/acpkit/getting-started/quickstart/)
- [CLI](https://vcoderun.github.io/acpkit/cli/)
- [Pydantic ACP Overview](https://vcoderun.github.io/acpkit/pydantic-acp/)
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
- [Dynamic Factory Agents](https://vcoderun.github.io/acpkit/examples/dynamic-factory/)
- [Testing](https://vcoderun.github.io/acpkit/testing/)

Reference docs:

- [`acpkit` API](https://vcoderun.github.io/acpkit/api/acpkit/)
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
