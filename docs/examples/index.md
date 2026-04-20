# Examples

ACP Kit keeps the maintained example set intentionally small. Each example should be broad enough to
demonstrate a real adapter shape instead of only one helper call.

## Maintained Pydantic Examples

Source directory:

- [`examples/pydantic/`](https://github.com/vcoderun/acpkit/tree/main/examples/pydantic)

| Example | What it demonstrates |
|---|---|
| [`finance_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/finance_agent.py) | session-aware finance workspace with ACP plans, approvals, mode-aware tool shaping, and projected note diffs |
| [`travel_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/travel_agent.py) | travel planning runtime with hook projection, approval-gated trip files, and prompt-model override behavior for media prompts |

## Maintained LangChain Examples

Source directory:

- [`examples/langchain/`](https://github.com/vcoderun/acpkit/tree/main/examples/langchain)

| Example | What it demonstrates |
|---|---|
| [`workspace_graph.py`](https://github.com/vcoderun/acpkit/blob/main/examples/langchain/workspace_graph.py) | plain LangChain graph wiring, module-level `graph`, session-aware `graph_from_session(...)`, and filesystem read/write projection |
| [`deepagents_graph.py`](https://github.com/vcoderun/acpkit/blob/main/examples/langchain/deepagents_graph.py) | DeepAgents compatibility wiring through `langchain-acp`, approvals, and DeepAgents projection presets |

## Maintained Transport Examples

Source directory:

- [`examples/acpremote/`](https://github.com/vcoderun/acpkit/tree/main/examples/acpremote)

| Example | What it demonstrates |
|---|---|
| [`serve_pydantic_finance.py`](https://github.com/vcoderun/acpkit/blob/main/examples/acpremote/serve_pydantic_finance.py) | adapts the maintained finance agent into ACP and exposes it remotely through `acpkit.serve_acp(...)` |
| [`serve_langchain_workspace.py`](https://github.com/vcoderun/acpkit/blob/main/examples/acpremote/serve_langchain_workspace.py) | adapts the maintained LangChain workspace graph into ACP and exposes it remotely through `acpkit.serve_acp(...)` |
| [`connect_mirror.py`](https://github.com/vcoderun/acpkit/blob/main/examples/acpremote/connect_mirror.py) | mirrors any remote ACP endpoint back into a local stdio ACP server with transport timing enabled |
| [`expose_codex.py`](https://github.com/vcoderun/acpkit/blob/main/examples/acpremote/expose_codex.py) | starts a stdio ACP command and exposes it through `acpremote` over WebSocket |
| [`connect_codex.py`](https://github.com/vcoderun/acpkit/blob/main/examples/acpremote/connect_codex.py) | mirrors a remote ACP endpoint back into a local stdio ACP server with transport timing enabled |

The transport examples cover both major remote-host paths:

- adapter-backed Python runtimes exposed through `acpkit` plus `acpremote`
- native ACP commands exposed directly through `acpremote`
- the local machine only mirrors the boundary
- `acpkit run --addr ...` and `connect_acp(...)` are the client-facing entry points

## Focused Recipes

Not every important integration seam needs another runnable demo file. Some patterns are better documented as focused recipes.

| Recipe | What it demonstrates |
|---|---|
| [Dynamic Factory Agents](dynamic-factory.md) | `agent_factory(session)` patterns for model switching, metadata routing, and session-aware agent construction |

## Recommended Reading Order

1. [Finance Agent](finance.md)
2. [LangChain Workspace Graph](langchain-workspace.md)
3. [Remote ACP Hosting](remote-hosting.md)
4. [Travel Agent](travel.md)
5. [DeepAgents Compatibility Example](deepagents.md)
6. [acpremote Overview](../acpremote.md)
7. [Dynamic Factory Agents](dynamic-factory.md)
