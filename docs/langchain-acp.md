# LangChain ACP Overview

`langchain-acp` is ACP Kit's graph-centric adapter for the LangChain stack:

- plain `create_agent(...)` graphs from LangChain
- compiled LangGraph graphs
- DeepAgents graphs built with `create_deep_agent(...)`

It is not a separate agent framework. The adapter takes a runtime that is already graph-shaped and exposes it through ACP without discarding the runtime's real semantics.

## Core Construction Paths

The public construction seams stay centered on graph ownership:

- `graph=...`
- `graph_factory=...`
- `graph_source=...`

Static graph:

```python
from langchain.agents import create_agent
from langchain_acp import run_acp

graph = create_agent(model="openai:gpt-5", tools=[])

run_acp(graph=graph)
```

Codex-backed graph:

```python
from codex_auth_helper import create_codex_chat_openai
from langchain.agents import create_agent
from langchain_acp import run_acp

graph = create_agent(
    model=create_codex_chat_openai("gpt-5.4"),
    tools=[],
    name="codex-graph",
)

run_acp(graph=graph)
```

Session-aware graph factory:

```python
from langchain.agents import create_agent
from langchain_acp import AcpSessionContext, run_acp


def graph_from_session(session: AcpSessionContext):
    model_name = session.session_model_id or "openai:gpt-5-mini"
    mode_name = session.session_mode_id or "default"
    return create_agent(
        model=model_name,
        tools=[],
        name=f"graph-{mode_name}",
        system_prompt=f"Operate in {mode_name} mode.",
    )


run_acp(graph_factory=graph_from_session)
```

Use `graph_factory=` when ACP session state should rebuild the upstream graph. That is the LangChain-side equivalent of `agent_factory=` in `pydantic-acp`.

If model construction depends on a local Codex login, pair this adapter with
`codex-auth-helper`. The helper owns auth parsing, refresh, and Responses-backed
`ChatOpenAI` construction; `langchain-acp` only owns ACP adaptation.

## What The Adapter Owns

`langchain-acp` carries the same ACP Kit design language that the repo's other adapters use:

- `AdapterConfig`
- explicit session stores and transcript replay
- provider-owned models, modes, and config options
- native ACP plan state with `TaskPlan`
- approval bridging
- capability bridges
- projection maps and event projection maps
- ACP-facing type exports in `langchain_acp.types`

The important difference is upstream shape, not ACP Kit architecture. On the LangChain side the adapter deals in graphs and middleware instead of model profiles and tool preparers.

### A Production-Shaped Configuration

This is the kind of shape the adapter is built for:

```python
from langchain.agents import create_agent
from langchain_acp import (
    AdapterConfig,
    DeepAgentsCompatibilityBridge,
    DeepAgentsProjectionMap,
    FileSessionStore,
    HttpRequestProjectionMap,
    StructuredEventProjectionMap,
    run_acp,
)


def graph_from_session(session):
    model_name = session.session_model_id or "openai:gpt-5-mini"
    return create_agent(
        model=model_name,
        tools=[],
        system_prompt=f"Work inside {session.cwd.name}.",
    )


config = AdapterConfig(
    session_store=FileSessionStore(root=".acpkit/langchain-sessions"),
    capability_bridges=[DeepAgentsCompatibilityBridge()],
    projection_maps=[
        DeepAgentsProjectionMap(),
        HttpRequestProjectionMap(),
    ],
    event_projection_maps=[StructuredEventProjectionMap()],
)

run_acp(graph_factory=graph_from_session, config=config)
```

The point is not to make the adapter magical. The point is to keep the host,
the graph, and the ACP surface aligned without inventing runtime state the graph
cannot really honor.

## Session Lifecycle And Replay

Session lifecycle is first-class:

- session creation
- load
- fork
- replay
- resume
- close

`SessionStore`, `MemorySessionStore`, and `FileSessionStore` work the same way they do elsewhere in ACP Kit. The LangChain adapter also replays stored transcript state when a session is loaded again, so session-scoped ACP state survives graph rebuilds instead of being treated as disposable transport history.

## Models, Modes, And Config Options

`langchain-acp` exposes ACP control surfaces only when the host runtime actually owns them.

Built-in config:

- `available_models`
- `available_modes`
- `default_model_id`
- `default_mode_id`

Provider-owned config:

- `SessionModelsProvider`
- `SessionModesProvider`
- `ConfigOptionsProvider`

These seams let the host own:

- a model picker
- a mode picker
- adapter-local config options

without baking product policy into the adapter core.

## Native Plans And `TaskPlan`

The adapter has ACP-native plan support, not only DeepAgents-style compatibility extraction.

Core surfaces:

- `TaskPlan`
- `PlanGenerationType`
- `acp_get_plan`
- `acp_set_plan`
- `acp_update_plan_entry`
- `acp_mark_plan_done`
- `native_plan_tools(...)`

Relevant ownership seams:

- `plan_mode_id`
- `default_plan_generation_type`
- `enable_plan_progress_tools`
- `PlanProvider`
- `NativePlanPersistenceProvider`

This means:

- a graph can publish ACP-native plan state
- a host can persist that plan state explicitly
- plan tools can be exposed only in the modes that really support them

DeepAgents `write_todos` integration exists as a compatibility layer, not as the core truth source.

## Approvals

LangChain already has an approval-friendly seam through `HumanInTheLoopMiddleware`. `langchain-acp` keeps that seam visible instead of flattening it into text.

The adapter surface is:

- `ApprovalBridge`
- `NativeApprovalBridge`
- ACP permission requests and resume flow

When the runtime really pauses for approval, the ACP session pauses for approval too.

## Capability Bridges And Graph Build Contributions

ACP Kit's bridge architecture remains intact in the LangChain adapter.

Built-in bridges:

- `ModelSelectionBridge`
- `ModeSelectionBridge`
- `ConfigOptionsBridge`
- `ToolSurfaceBridge`
- `DeepAgentsCompatibilityBridge`

Graph-build contributions are aggregated through:

- `GraphBridgeBuilder`
- `GraphBuildContributions`

That contribution seam lets bridges influence:

- middleware
- tools
- system prompt parts
- response format
- interrupt configuration
- graph metadata

without turning the adapter runtime into a monolith.

## Projection Maps

Tool projection is first-class:

- `ProjectionMap`
- `FileSystemProjectionMap`
- `CommunityFileManagementProjectionMap`
- `WebSearchProjectionMap`
- `HttpRequestProjectionMap`
- `BrowserProjectionMap`
- `CommandProjectionMap`
- `FinanceProjectionMap`
- `CompositeProjectionMap`
- `DeepAgentsProjectionMap`

These maps convert raw tool activity into ACP-visible updates such as:

- file reads
- file diffs
- searches
- shell command previews
- terminal output

This is the seam that makes LangChain tools feel ACP-native instead of opaque.

### Real Tool Families

The current public projection families are intentionally concrete:

- `FileSystemProjectionMap` for file reads and writes
- `CommunityFileManagementProjectionMap` for `langchain-community` file tools
- `WebSearchProjectionMap` for search tool families
- `HttpRequestProjectionMap` for `requests_*` HTTP request tools
- `BrowserProjectionMap` for browser/navigation tools
- `CommandProjectionMap` for shell/terminal execution
- `FinanceProjectionMap` for finance and news lookup tools
- `DeepAgentsProjectionMap` for DeepAgents compatibility

`WebFetchProjectionMap` remains as a compatibility alias for
`HttpRequestProjectionMap`.

## Event Projection Maps

Some LangChain and LangGraph products emit callback payloads or event-shaped data that should become ACP transcript updates.

That path is modeled separately:

- `EventProjectionMap`
- `StructuredEventProjectionMap`
- `CompositeEventProjectionMap`

This keeps tool projection and callback-event projection distinct instead of overloading one mechanism for both.

## DeepAgents Compatibility

DeepAgents graphs are not treated as a separate runtime. They are just another compiled graph target.

Use the compatibility pieces only where they add truthful ACP behavior:

- `DeepAgentsCompatibilityBridge`
- `DeepAgentsProjectionMap`

Typical wiring:

```python
from deepagents import create_deep_agent
from langchain_acp import (
    AdapterConfig,
    DeepAgentsCompatibilityBridge,
    DeepAgentsProjectionMap,
    create_acp_agent,
)

graph = create_deep_agent(...)

acp_agent = create_acp_agent(
    graph=graph,
    config=AdapterConfig(
        capability_bridges=[DeepAgentsCompatibilityBridge()],
        projection_maps=[DeepAgentsProjectionMap()],
    ),
)
```

That compatibility layer keeps `write_todos` plan extraction and familiar filesystem or shell projection behavior available without making DeepAgents policy the core adapter architecture.

## Migration From `deepagents-acp`

Use `langchain-acp` when you want ACP Kit's reusable seams instead of a bespoke ACP runtime:

- ACP-native plan mode and `TaskPlan`
- session providers for models, modes, and config
- session stores and transcript replay
- reusable capability bridges instead of hard-coded runtime policy
- event projection maps and richer tool projection
- the same root `acpkit run ...` target resolver used by the Pydantic adapter

Keep DeepAgents-specific product policy outside the adapter core:

- shell allowlists
- product-specific danger heuristics
- branded mode labels or presets
- backend routing choices that belong to the host app

## Maintained Examples

- [Codex-Backed LangChain Graph](examples/langchain-codex.md)
- [LangChain Workspace Graph](examples/langchain-workspace.md)
- [DeepAgents Compatibility Example](examples/deepagents.md)

## Reading Order

If you are integrating `langchain-acp` in a real product:

1. Read [LangChain Quickstart](getting-started/langchain-quickstart.md).
2. Read [AdapterConfig](langchain-acp/adapter-config.md).
3. Read [Session State and Lifecycle](langchain-acp/session-state.md).
4. Read [Models, Modes, and Config](langchain-acp/runtime-controls.md).
5. Read [Plans, Thinking, and Approvals](langchain-acp/plans-thinking-approvals.md).
6. Read [Providers](langchain-acp/providers.md).
7. Read [Bridges](langchain-acp/bridges.md).
8. Read [Projections and Event Projection Maps](langchain-acp/projections.md).
9. Read the maintained LangChain examples.

## API Reference

- [langchain_acp API](api/langchain_acp.md)
