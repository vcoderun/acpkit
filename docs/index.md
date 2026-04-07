# ACP Kit

ACP Kit is a monorepo for ACP-facing runtime packages. The root `acpkit` package handles CLI
dispatch, `pydantic-acp` adapts `pydantic_ai.Agent` instances to ACP, and `codex-auth-helper`
provides a Codex-backed `OpenAIResponsesModel` helper.

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

Start an ACP server directly from Python:

```python
from pydantic_ai import Agent
from pydantic_acp import run_acp

agent = Agent("openai:gpt-5", name="demo-agent")
run_acp(agent=agent)
```

## Key Concepts

- `run_acp(...)`: start an ACP server from a direct agent, factory, or `AgentSource`
- `create_acp_agent(...)`: create the ACP agent object without running it yet
- `AdapterConfig`: configure persistence, approvals, providers, bridges, and projections
- `FileSessionStore`: keep ACP sessions across process restarts
- `FileSystemProjectionMap`: render file reads, writes, and bash tools as richer ACP content
- `HookProjectionMap`: render observed `Hooks` lifecycle events as ACP updates

## Documentation Map

- `cli.md`: root CLI command semantics
- `pydantic-acp.md`: adapter API, `AdapterConfig`, factories, approvals, projections, providers, and host backends
- `../examples/pydantic/README.md`: focused examples and runnable demos
- `helpers.md`: helper packages and Codex model integration
- `providers.md`: provider interfaces and host-owned session state
- `bridges.md`: capability bridges, hook projection, and existing hook introspection
- `host-backends.md`: filesystem and terminal helpers
- `testing.md`: behavioral test coverage and validation commands
- `about/index.md`: design goals and project position
