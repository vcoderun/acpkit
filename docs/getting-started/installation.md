# Installation

ACP Kit ships as a workspace, but most users usually start from one of five install paths:

1. the root CLI plus the Pydantic adapter
2. the root CLI plus the LangChain adapter
3. the standalone adapter package that matches their runtime
4. the standalone `acpremote` transport helper
5. the standalone `codex-auth-helper` package

## Install Stable v1

Install every maintained integration:

```bash
uv add "acpkit[all]>=1.0.0,<2.0.0"
```

```bash
pip install "acpkit[all]>=1.0.0,<2.0.0"
```

The root extras require matching v1-generation adapter, helper, and transport
packages. See the
[release notes](https://vcoderun.github.io/acpkit/releases/acpkit-1.0.0/) and
[versioning policy](https://vcoderun.github.io/acpkit/versioning/).

## Install The Root CLI

Use this path when you want `acpkit run ...` and `acpkit launch ...`.

For `pydantic_ai.Agent` targets:

```bash
uv add "acpkit[pydantic]"
```

```bash
pip install "acpkit[pydantic]"
```

For LangChain, LangGraph, or DeepAgents targets:

```bash
uv add "acpkit[langchain]"
```

```bash
pip install "acpkit[langchain]"
```

Add the launch helper when you want to boot agents through Toad ACP:

```bash
uv add "acpkit[pydantic,launch]"
```

```bash
pip install "acpkit[pydantic,launch]"
```

## Install The Adapter Package Directly

Use this when you only need the Python adapter API and do not care about the root CLI.

For `pydantic_ai.Agent` runtimes:

```bash
uv add pydantic-acp
```

```bash
pip install pydantic-acp
```

`pydantic-acp` supports `pydantic-ai-slim>=2.9.0,<=2.22.0` and pins the ACP
protocol version it integrates against. Pydantic AI V1 and releases before
2.9.0 are not supported.

For LangChain, LangGraph, or DeepAgents runtimes:

```bash
uv add langchain-acp
```

```bash
pip install langchain-acp
```

Add the optional DeepAgents helpers when needed:

```bash
uv add "langchain-acp[deepagents]"
```

```bash
pip install "langchain-acp[deepagents]"
```

The current minimum versions are `langchain>=1.3.11`, `langgraph>=1.2.7`, and, when the optional
extra is installed, `deepagents>=0.6.12`. ACP Kit's CI resolves and tests these three versions
together rather than validating them in isolation.

`langchain-acp` is the direct dependency when your app already owns a compiled LangGraph graph or a LangChain `create_agent(...)` graph and you want ACP Kit's adapter seams without the root CLI.

## Install The Codex Helper

Use this when you want a Codex-backed helper for Pydantic AI or LangChain:

```bash
uv add codex-auth-helper
```

```bash
pip install codex-auth-helper
```

For LangChain usage, install the optional extra:

```bash
uv add "codex-auth-helper[langchain]"
```

```bash
pip install "codex-auth-helper[langchain]"
```

This helper expects an existing local Codex login and reads `~/.codex/auth.json` by default.

## Install ACP Remote

Use this when you already have an ACP agent or stdio ACP command and only need remote transport:

```bash
uv add acpremote
```

```bash
pip install acpremote
```

`acpremote` is transport-only. It does not adapt a framework runtime into ACP; it exposes or mirrors an ACP boundary that already exists.

It also installs the `acpremote` executable:

```bash
acpremote expose --host 0.0.0.0 --port 8080 -- npx @zed-industries/codex-acp
acpremote mirror ws://remote.example.com:8080/acp/ws
```

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
