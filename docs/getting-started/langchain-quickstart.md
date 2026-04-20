# LangChain Quickstart

This guide takes you from a normal LangChain or LangGraph graph to a useful ACP server.

## 1. Start From A Real Graph

The adapter expects a compiled graph-shaped runtime. The smallest path is a LangChain `create_agent(...)` graph:

```python
from langchain.agents import create_agent

graph = create_agent(
    model="openai:gpt-5",
    tools=[],
    system_prompt="Answer directly and keep responses short.",
)
```

You can also start from:

- a compiled LangGraph graph
- a DeepAgents graph built with `create_deep_agent(...)`

## 2. Expose The Graph Through ACP

Wrap the graph with `run_acp(...)`:

```python
from langchain_acp import run_acp

run_acp(graph=graph)
```

That is the smallest supported integration.

Equivalent one-file example:

```python
from langchain.agents import create_agent
from langchain_acp import run_acp

graph = create_agent(
    model="openai:gpt-5",
    tools=[],
    system_prompt="Answer directly and keep responses short.",
)


if __name__ == "__main__":
    run_acp(graph=graph)
```

## 3. Add Session Persistence And Replay

Session persistence works the same way ACP Kit works elsewhere: through an explicit session store.

```python
from pathlib import Path

from langchain_acp import AdapterConfig, FileSessionStore, run_acp

run_acp(
    graph=graph,
    config=AdapterConfig(
        session_store=FileSessionStore(root=Path(".acp-sessions")),
    ),
)
```

At this point you already have:

- session creation and replay
- ACP transcript persistence
- load, fork, resume, and close behavior
- transcript replay when the session is loaded again

## 4. Let ACP Session State Rebuild The Graph

If the ACP session should affect graph construction, use `graph_factory=`:

```python
from langchain.agents import create_agent
from langchain_acp import AcpSessionContext, run_acp


def graph_from_session(session: AcpSessionContext):
    model_name = session.session_model_id or "openai:gpt-5-mini"
    mode_name = session.session_mode_id or "default"
    return create_agent(
        model=model_name,
        tools=[],
        system_prompt=f"Operate in {mode_name} mode.",
        name=f"graph-{mode_name}",
    )


run_acp(graph_factory=graph_from_session)
```

This is the LangChain-side equivalent of `agent_factory=` in `pydantic-acp`.

## 5. Add ACP-visible Models, Modes, Plans, And Approvals

The core config seam is still `AdapterConfig`:

```python
from acp.schema import ModelInfo, SessionMode
from langchain_acp import AdapterConfig

config = AdapterConfig(
    available_models=[
        ModelInfo(model_id="fast", name="Fast"),
        ModelInfo(model_id="smart", name="Smart"),
    ],
    available_modes=[
        SessionMode(id="ask", name="Ask"),
        SessionMode(id="agent", name="Agent"),
    ],
    plan_mode_id="plan",
    enable_plan_progress_tools=True,
)
```

That surface can then be backed by:

- `SessionModelsProvider`
- `SessionModesProvider`
- `ConfigOptionsProvider`
- `PlanProvider`
- `NativePlanPersistenceProvider`

When the graph already uses `HumanInTheLoopMiddleware`, `langchain-acp` preserves approval requests through ACP instead of flattening them into text.

## 6. Add Projection Maps

Projection maps are what make filesystem and shell tools feel ACP-native instead of opaque:

```python
from langchain_acp import AdapterConfig, FileSystemProjectionMap

config = AdapterConfig(
    projection_maps=[
        FileSystemProjectionMap(
            read_tool_names=frozenset({"read_file"}),
            write_tool_names=frozenset({"write_file", "edit_file"}),
            search_tool_names=frozenset({"glob", "grep", "ls"}),
            execute_tool_names=frozenset({"execute"}),
        )
    ]
)
```

If the runtime emits structured callback or ACP-like event payloads, `event_projection_maps` can translate them into ACP transcript updates as well.

Common preset families:

- `WebSearchProjectionMap` for search toolkits
- `HttpRequestProjectionMap` for `requests_*` HTTP tools
- `BrowserProjectionMap` for browser/navigation tools
- `CommunityFileManagementProjectionMap` for community file tools
- `FinanceProjectionMap` for finance and news toolkits

Use `DeepAgentsProjectionMap` only when you want the compatibility defaults
that match the maintained DeepAgents example.

## 7. Run Through The CLI

If you installed the root package:

```bash
acpkit run my_graph_module
acpkit run my_graph_module:graph
acpkit run examples.langchain.workspace_graph:graph
```

`acpkit` resolves the target, detects the last defined supported graph target when needed, and dispatches it to `langchain-acp`.

## 8. DeepAgents Compatibility

DeepAgents graphs are just another compiled graph input, but ACP Kit keeps the product-specific behavior opt-in:

```python
from deepagents import create_deep_agent
from langchain_acp import (
    AdapterConfig,
    DeepAgentsCompatibilityBridge,
    DeepAgentsProjectionMap,
    run_acp,
)

graph = create_deep_agent(...)

run_acp(
    graph=graph,
    config=AdapterConfig(
        capability_bridges=[DeepAgentsCompatibilityBridge()],
        projection_maps=[DeepAgentsProjectionMap()],
    ),
)
```

Use that compatibility layer when you want:

- `write_todos` plan extraction
- familiar DeepAgents filesystem and shell projection defaults
- DeepAgents-flavored session metadata

## 9. Know What To Read Next

- Want the runtime architecture? Read [LangChain ACP Overview](../langchain-acp.md).
- Want every `AdapterConfig` knob? Read [AdapterConfig](../langchain-acp/adapter-config.md).
- Want session durability and replay? Read [Session State and Lifecycle](../langchain-acp/session-state.md).
- Want models, modes, and config ownership? Read [Models, Modes, and Config](../langchain-acp/runtime-controls.md).
- Want plans and approvals? Read [Plans, Thinking, and Approvals](../langchain-acp/plans-thinking-approvals.md).
- Want maintained examples? Read [LangChain Workspace Graph](../examples/langchain-workspace.md) and [DeepAgents Compatibility Example](../examples/deepagents.md).
- Want host-owned state? Read [Providers](../langchain-acp/providers.md).
- Want ACP-visible capability seams? Read [Bridges](../langchain-acp/bridges.md).
- Want richer tool and event rendering? Read [Projections and Event Projection Maps](../langchain-acp/projections.md).
