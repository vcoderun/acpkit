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

## Helper And CLI Recipes

Helper packages do not ship separate public example trees under the repo root.
Their maintained operator recipes live in the package overviews and in the
skill bundles under `.agents/skills/.../examples`.

## Documented Remote-Host Pattern

Remote hosting is documented as a focused guide rather than as another maintained example source
tree.

The guide covers both major remote-host paths:

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
