# langchain-acp

`langchain-acp` exposes LangChain, LangGraph, and DeepAgents graphs through ACP Kit.

It keeps ACP Kit's adapter architecture intact while staying graph-centric on the LangChain side:

- `graph=...`
- `graph_factory=...`
- `graph_source=...`

## Install

```bash
uv add langchain-acp
```

```bash
pip install langchain-acp
```

With optional DeepAgents compatibility:

```bash
uv add "langchain-acp[deepagents]"
```

```bash
pip install "langchain-acp[deepagents]"
```

Contributor setup from the monorepo root:

```bash
uv sync --extra dev --extra langchain
```

## Quickstart

```python
from langchain.agents import create_agent
from langchain_acp import run_acp

graph = create_agent(model="openai:gpt-5", tools=[])

run_acp(graph=graph)
```

If ACP session state should affect graph construction, use `graph_factory=`:

```python
from langchain.agents import create_agent
from langchain_acp import AcpSessionContext, create_acp_agent


def graph_from_session(session: AcpSessionContext):
    mode_name = session.session_mode_id or "default"
    return create_agent(model="openai:gpt-5", tools=[], name=f"graph-{mode_name}")


acp_agent = create_acp_agent(graph_factory=graph_from_session)
```

## What The Adapter Covers

`langchain-acp` carries the same ACP Kit seams that matter elsewhere in the repo, but mapped onto graph ownership instead of agent ownership:

- session stores and transcript replay
- model, mode, and config-option providers
- native plan state through `TaskPlan`
- approval bridging from `HumanInTheLoopMiddleware`
- capability bridges and graph-build contributions
- tool projection maps and event projection maps
- `graph`, `graph_factory`, and `graph_source`
- DeepAgents compatibility helpers where they add truthful ACP behavior

That means the adapter can expose:

- plain LangChain `create_agent(...)` graphs
- compiled LangGraph graphs
- DeepAgents graphs

without collapsing everything into a bespoke ACP runtime.

## Session-owned Graph Rebuilds

If ACP session state should decide which graph gets built, `graph_factory=` is the intended seam:

```python
from langchain.agents import create_agent
from langchain_acp import AcpSessionContext, AdapterConfig, MemorySessionStore, run_acp


def graph_from_session(session: AcpSessionContext):
    mode_name = session.session_mode_id or "default"
    model_name = session.session_model_id or "openai:gpt-5-mini"
    return create_agent(
        model=model_name,
        tools=[],
        name=f"graph-{mode_name}",
        system_prompt=f"Operate in {mode_name} mode.",
    )


run_acp(
    graph_factory=graph_from_session,
    config=AdapterConfig(session_store=MemorySessionStore()),
)
```

Use this when workspace path, mode, model, or session metadata should rebuild the graph dynamically.

## DeepAgents Compatibility

DeepAgents graphs are supported as compiled LangGraph targets.

Use the compatibility helpers only when they add real value:

- `DeepAgentsCompatibilityBridge`
- `DeepAgentsProjectionMap`

Maintained examples:

- [workspace_graph.py](https://github.com/vcoderun/acpkit/blob/main/examples/langchain/workspace_graph.py)
- [deepagents_graph.py](https://github.com/vcoderun/acpkit/blob/main/examples/langchain/deepagents_graph.py)

Docs:

- <https://vcoderun.github.io/acpkit/langchain-acp/>
- <https://vcoderun.github.io/acpkit/getting-started/langchain-quickstart/>
- <https://vcoderun.github.io/acpkit/examples/langchain-workspace/>
- <https://vcoderun.github.io/acpkit/examples/deepagents/>
