# Installation

ACP Kit ships as a workspace, but most users usually start from one of five install paths:

1. the root CLI plus the Pydantic adapter
2. the root CLI plus the LangChain adapter
3. the standalone adapter package that matches their runtime
4. the standalone `acpremote` transport helper
5. the standalone `codex-auth-helper` package

## Install The Root CLI

Use this path when you want `acpkit run ...` and `acpkit launch ...`.

For `pydantic_ai.Agent` targets:

```bash
uv pip install "acpkit[pydantic]"
```

```bash
pip install "acpkit[pydantic]"
```

For LangChain, LangGraph, or DeepAgents targets:

```bash
uv pip install "acpkit[langchain]"
```

```bash
pip install "acpkit[langchain]"
```

Add the launch helper when you want to boot agents through Toad ACP:

```bash
uv pip install "acpkit[pydantic,launch]"
```

```bash
pip install "acpkit[pydantic,launch]"
```

## Install The Adapter Package Directly

Use this when you only need the Python adapter API and do not care about the root CLI.

For `pydantic_ai.Agent` runtimes:

```bash
uv pip install pydantic-acp
```

```bash
pip install pydantic-acp
```

`pydantic-acp` pins the ACP and Pydantic AI versions it integrates against, so it is the safest direct dependency when you are embedding the adapter inside another application.

For LangChain, LangGraph, or DeepAgents runtimes:

```bash
uv pip install langchain-acp
```

```bash
pip install langchain-acp
```

Add the optional DeepAgents helpers when needed:

```bash
uv pip install "langchain-acp[deepagents]"
```

```bash
pip install "langchain-acp[deepagents]"
```

`langchain-acp` is the direct dependency when your app already owns a compiled LangGraph graph or a LangChain `create_agent(...)` graph and you want ACP Kit's adapter seams without the root CLI.

## Install The Codex Helper

Use this when you want a Codex-backed helper for Pydantic AI or LangChain:

```bash
uv pip install codex-auth-helper
```

```bash
pip install codex-auth-helper
```

For LangChain usage, install the optional extra:

```bash
uv pip install "codex-auth-helper[langchain]"
```

```bash
pip install "codex-auth-helper[langchain]"
```

This helper expects an existing local Codex login and reads `~/.codex/auth.json` by default.

## Install ACP Remote

Use this when you already have an ACP agent or stdio ACP command and only need remote transport:

```bash
uv pip install acpremote
```

```bash
pip install acpremote
```

`acpremote` is transport-only. It does not adapt a framework runtime into ACP; it exposes or mirrors an ACP boundary that already exists.

## Development Setup

From the repo root:

```bash
uv sync --extra dev --extra docs --extra pydantic --extra langchain --extra codex --extra remote
```

```bash
pip install -e ".[dev,docs,pydantic,langchain,codex,remote]"
```

That gives you:

- runtime packages
- docs tooling
- test, lint, and type-check tools
- the local Codex helper package

## Validation Commands

Repo-root checks:

```bash
uv run ruff check
uv run ty check
uv run basedpyright
make tests
make check
```

Docs preview:

```bash
uv run --extra docs --extra pydantic --extra langchain --extra codex mkdocs serve --dev-addr 127.0.0.1:8080
```

## Which Package Should You Reach For?

| You want to... | Install |
|---|---|
| resolve Pydantic AI targets from the command line | `acpkit[pydantic]` |
| resolve LangChain or LangGraph targets from the command line | `acpkit[langchain]` |
| embed the ACP adapter in a Pydantic AI app | `pydantic-acp` |
| embed the ACP adapter in a LangChain or LangGraph app | `langchain-acp` |
| expose or mirror an existing ACP server over WebSocket | `acpremote` |
| build a Codex-backed Pydantic AI model | `codex-auth-helper` |
| build a Codex-backed LangChain chat model | `codex-auth-helper[langchain]` |
| work on the repo itself | repo checkout + `uv sync --extra dev --extra docs --extra pydantic --extra langchain --extra codex --extra remote` |
