# langchain-acp Examples

These examples expose real LangChain, LangGraph, and DeepAgents graphs through
ACP. Model and mode choices belong to the ACP session and rebuild the graph
through `graph_factory`.

They are tested against LangChain 1.4.7, LangGraph 1.2.18, and DeepAgents
0.7.32.

## Setup

```bash
uv sync --extra dev --extra langchain --extra codex
```

An existing local Codex login is required because the examples use
`codex-auth-helper`.

## Codex Graph

This is the smallest session-aware Codex-backed LangChain graph:

```bash
CODEX_MODEL="gpt-5.4" \
uv run python -m examples.langchain.codex_graph
```

It demonstrates explicit Codex `instructions=`, ACP model and mode selection,
and graph reconstruction per session.

## Workspace Graph

```bash
CODEX_MODEL="gpt-5.4" \
uv run python -m examples.langchain.workspace_graph
```

The graph has real file tools scoped to `<session cwd>/agent_demos/workspace-graph/`.
`FileSystemProjectionMap` renders reads and writes truthfully in ACP clients.
Path traversal is rejected before filesystem access.

Useful prompts:

- `List the workspace files and summarize README.md.`
- `In edit mode, write notes/review.md with three review points.`
- `In plan mode, propose the steps before changing any file.`

## DeepAgents Graph

```bash
CODEX_MODEL="gpt-5.4" \
uv run python -m examples.langchain.deepagents_graph
```

This example uses the real `create_deep_agent(...)` constructor, ACP-native
plan tools, and DeepAgents compatibility bridges. Its filesystem tools return
fixed bounded content and acknowledge writes without mutating the host. This
keeps the compatibility example deterministic and avoids workspace permission
failures.

## Session Storage

All examples use `FileSessionStore` under `agent_demos/acp-sessions/`. Override
the parent directory with `ACP_EXAMPLE_SESSION_DIR`. The local file store is
intended for one process; use an application-owned store for multiple replicas.

## Production Boundaries

- Keep Codex auth files private and never copy them into containers or logs.
- Replace fixed demo filesystem tools with an allowlisted storage backend when
  real mutation is required.
- Keep projection maps aligned with actual graph tool names and schemas.
- Terminate remote ACP traffic at an authenticated TLS boundary.

Detailed walkthroughs are available in the
[LangChain quickstart](https://vcoderun.github.io/acpkit/getting-started/langchain-quickstart/)
and [examples index](https://vcoderun.github.io/acpkit/examples/).
