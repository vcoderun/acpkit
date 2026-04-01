# ACP Kit

ACP Kit is a monorepo for ACP adapters. The root `acpkit` package provides adapter-aware CLI dispatch, and the first implemented adapter package is `pydantic-acp`, which turns `pydantic_ai.Agent` instances into ACP agents.

## Repository Layout

```text
src/acpkit/                      Root CLI and adapter dispatch
packages/pydantic-acp/           Pydantic AI -> ACP adapter
tests/                           Behavioral and integration tests
docs/                            Project documentation
```

## Workspace Status

Implemented packages:

- `acpkit`: root CLI and target resolution
- `pydantic-acp`: `pydantic_ai.Agent` to ACP adapter

Planned package slots:

- `x-acp`: reserved in the monorepo layout, not implemented yet

## Installation

Production:

```bash
uv pip install "acpkit[pydantic]"
```

```bash
pip install "acpkit[pydantic]"
```

Development:

```bash
uv sync --extra dev --extra docs --extra pydantic
```

```bash
pip install -e ".[dev,docs,pydantic]"
```

## CLI Quick Start

Run a supported agent target through ACP:

```bash
acpkit run my_agent
acpkit run my_agent:agent
acpkit run my_agent:agent -p ./agent_home
```

`acpkit` resolves `module` or `module:attribute` targets, auto-detects `pydantic_ai.Agent` instances, and dispatches them to the installed adapter package. If only the module is given, it selects the last defined `pydantic_ai.Agent` instance in that module.

If the matching adapter extra is not installed, `acpkit` fails with an install hint such as `uv pip install "acpkit[pydantic]"`.

## Implemented Milestones

| Milestone | Status | Scope |
| --- | --- | --- |
| 1 | complete | Bare ACP adapter, session lifecycle, transcript replay, generic tool projection, output serialization |
| 2 | complete | Session-local model selection and ACP model exposure |
| 3 | complete | Native deferred approval bridge and persistent approval choices |
| 4 | complete | Static agent, session-aware factory, and `AgentSource` integration |
| 5 | complete | Provider interfaces for models, modes, config options, plans, and approval state |
| 6 | complete | Capability bridges, bridge builder integration, MCP-aware projection, history and hook mapping |
| 7 | complete | ACP client-backed host backends for filesystem and terminal access |

## Milestone 7 Phases

| Phase | Status | Scope |
| --- | --- | --- |
| 1 | complete | `ClientFilesystemBackend` |
| 2 | complete | `ClientTerminalBackend` |
| 3 | complete | `ClientHostContext` and session-scoped host helper usage |

## Current Feature Surface

- Root CLI: `acpkit run module`, `acpkit run module:attribute`, repeated `-p/--path`
- Adapter bootstrap: `run_acp(...)`, `create_acp_agent(...)`
- Agent inputs: direct `Agent`, sync or async `agent_factory`, custom `AgentSource`
- Session features: create, load, list, fork, resume, close, transcript replay, history replay
- Session controls: model selection, mode selection, config options, plan updates
- Approval flow: ACP permission requests mapped to Pydantic AI deferred approvals
- Capability bridges: hooks, prepare-tools, history processors, MCP metadata and classification
- Host helpers: session-scoped ACP filesystem and terminal adapters

## Shortest Package Examples

Static agent:

```python
from pydantic_acp import run_acp
from pydantic_ai import Agent

agent = Agent("test")
run_acp(agent=agent)
```

Session-aware factory:

```python
from pydantic_acp import AcpSessionContext, create_acp_agent
from pydantic_ai import Agent

def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    return Agent("test", name=f"agent-{session.cwd.name}")

acp_agent = create_acp_agent(agent_factory=build_agent)
```

## Development

ACP Kit uses `uv` for dependency management and tool execution. The canonical local checks are:

```bash
uv run ruff check
uv run ty check
uv run basedpyright
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 python3.11 -B -m pytest tests/pydantic tests/test_acpkit_cli.py -q
make check
```

To preview the docs locally:

```bash
uv run mkdocs serve --dev-addr 127.0.0.1:8080
```

## Documentation Map

- `docs/index.md`: workspace overview and documentation map
- `docs/cli.md`: root `acpkit` CLI behavior
- `docs/pydantic-acp.md`: adapter architecture and milestone coverage
- `docs/providers.md`: provider seams
- `docs/bridges.md`: capability bridge system
- `docs/host-backends.md`: filesystem and terminal helpers
- `docs/testing.md`: behavioral test surface
