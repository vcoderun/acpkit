# Installation

ACP Kit ships as a workspace, but most users start from one of three install paths:

1. the root CLI plus the Pydantic adapter
2. the standalone `pydantic-acp` package
3. the standalone `codex-auth-helper` package

## Install The Root CLI

Use this path when you want `acpkit run ...` and `acpkit launch ...`.

Production:

```bash
uv pip install "acpkit[pydantic]"
```

```bash
pip install "acpkit[pydantic]"
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

```bash
uv pip install pydantic-acp
```

```bash
pip install pydantic-acp
```

`pydantic-acp` pins the ACP and Pydantic AI versions it integrates against, so it is the safest direct dependency when you are embedding the adapter inside another application.

## Install The Codex Helper

Use this when you want a Codex-backed `OpenAIResponsesModel` factory for Pydantic AI:

```bash
uv pip install codex-auth-helper
```

```bash
pip install codex-auth-helper
```

This helper expects an existing local Codex login and reads `~/.codex/auth.json` by default.

## Development Setup

From the repo root:

```bash
uv sync --extra dev --extra docs --extra pydantic --extra codex
```

```bash
pip install -e ".[dev,docs,pydantic,codex]"
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
uv run --extra docs --extra pydantic --extra codex mkdocs serve --dev-addr 127.0.0.1:8080
```

## Which Package Should You Reach For?

| You want to... | Install |
|---|---|
| resolve Python targets from the command line | `acpkit[pydantic]` |
| embed the ACP adapter inside an existing Python app | `pydantic-acp` |
| build a Codex-backed Pydantic AI model | `codex-auth-helper` |
| work on the repo itself | repo checkout + `uv sync --extra dev --extra docs --extra pydantic --extra codex` |
