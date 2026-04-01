# ACP Kit

ACP Kit is a monorepo for ACP adapter packages. The root `acpkit` package handles CLI dispatch, and the current adapter implementation is `pydantic-acp`.

## Packages

- `acpkit`: root CLI and adapter dispatch
- `pydantic-acp`: ACP adapter for `pydantic_ai.Agent`
- `x-acp`: reserved package slot, not implemented yet

## Milestone Status

| Milestone | Status | Notes |
| --- | --- | --- |
| 1 | complete | Bare ACP adapter |
| 2 | complete | Session-local model selection |
| 3 | complete | Native deferred approvals |
| 4 | complete | Factory and source integration |
| 5 | complete | Provider interfaces |
| 6 | complete | Capability bridges |
| 7 | complete | Host backends |

## Milestone 7 Phases

| Phase | Status | Notes |
| --- | --- | --- |
| 1 | complete | ACP filesystem backend |
| 2 | complete | ACP terminal backend |
| 3 | complete | Combined host context |

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
- `pydantic-acp.md`: adapter architecture, public API, and milestone coverage
- `providers.md`: provider interfaces and host-owned session state
- `bridges.md`: capability bridge system and bridge builder
- `host-backends.md`: filesystem and terminal helpers
- `testing.md`: behavioral test coverage and validation commands
- `about/index.md`: design goals and project position
