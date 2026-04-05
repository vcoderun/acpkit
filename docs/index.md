# ACP Kit

ACP Kit is a monorepo for ACP-facing runtime packages. The root `acpkit` package handles CLI dispatch, `pydantic-acp` provides the ACP adapter, and `codex-auth-helper` provides a small Codex-to-`pydantic-ai` model helper.

## Packages

- `acpkit`: root CLI and adapter dispatch
- `pydantic-acp`: ACP adapter for `pydantic_ai.Agent`
- `codex-auth-helper`: Codex auth file and token refresh helper for `OpenAIResponsesModel`

## Status

- `pydantic-acp` milestone 1-7 scope is implemented
- the root CLI dispatches installed adapter extras
- the helper workspace includes `codex-auth-helper`

## Quick Start

Development install:

```bash
uv sync --extra dev --extra docs --extra pydantic
```

Run a Pydantic AI agent through ACP:

```bash
acpkit run my_agent
acpkit run my_agent:agent
acpkit run my_agent:agent -p ./agent_home
```

## Documentation Map

- `cli.md`: root CLI command semantics
- `pydantic-acp.md`: adapter architecture, public API, runtime controls, and projection maps
- `../examples/pydantic/README.md`: focused examples and runnable demos
- `helpers.md`: helper packages and Codex model integration
- `providers.md`: provider interfaces and host-owned session state
- `bridges.md`: capability bridges, hook projection, and existing hook introspection
- `host-backends.md`: filesystem and terminal helpers
- `testing.md`: behavioral test coverage and validation commands
- `about/index.md`: design goals and project position
