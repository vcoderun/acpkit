# ACP Kit

ACP Kit is a monorepo for ACP-facing agent runtime packages.

- `acpkit` is the root CLI and target resolver
- `pydantic-acp` turns `pydantic_ai.Agent` instances into ACP agents
- `codex-auth-helper` turns a local Codex login into a `pydantic-ai` Responses model

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

## Status

- `pydantic-acp` milestone 1-7 scope is implemented
- `acpkit` CLI supports Click-based `run` dispatch for installed adapters
- `codex-auth-helper` provides `create_codex_responses_model(...)` for Codex-backed `pydantic-ai` usage

## Current Feature Surface

- Root CLI: `acpkit run module`, `acpkit run module:attribute`, repeated `-p/--path`
- Adapter bootstrap: `run_acp(...)`, `create_acp_agent(...)`
- Agent inputs: direct `Agent`, sync or async `agent_factory`, custom `AgentSource`
- Session features: create, load, list, fork, resume, close, transcript replay, history replay
- Session persistence: in-memory by default, or file-backed with `FileSessionStore`
- Session controls: ACP model state, mode state, config options, plan updates
- Session slash commands: `/model`, `/tools`, `/hooks`, `/mcp-servers`
- Approval flow: ACP permission requests mapped to Pydantic AI deferred approvals
- Projection maps: filesystem read/write diffs and bash command previews
- Capability bridges: hooks, prepare-tools, history processors, MCP metadata and classification
- Hook introspection: existing `Hooks` capabilities can be surfaced into ACP updates and `/hooks`
- Host helpers: session-scoped ACP filesystem and terminal adapters
- Codex helper: `create_codex_responses_model(...)` from `packages/helpers/codex-auth-helper`

`/model` notes:

- `/model` prints the current model id
- `/model provider:model` switches the current session model
- Codex-backed selection must be explicit: `/model codex:gpt-5`

## Quick Examples

Static agent:

```python
from pydantic_ai import Agent
from pydantic_acp import run_acp

agent = Agent("test")
run_acp(agent=agent)
```

Configured adapter with file-backed sessions:

```python
from pathlib import Path

from pydantic_ai import Agent
from pydantic_acp import AdapterConfig, FileSessionStore, run_acp

agent = Agent("test", name="demo-agent")

run_acp(
    agent=agent,
    config=AdapterConfig(
        session_store=FileSessionStore(base_dir=Path(".acp-sessions")),
    ),
)
```

Session-aware factory:

```python
from pydantic_ai import Agent
from pydantic_acp import AcpSessionContext, create_acp_agent

def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    return Agent("test", name=f"agent-{session.cwd.name}")

acp_agent = create_acp_agent(agent_factory=build_agent)
```

Filesystem diff projection:

```python
from pydantic_acp import FileSystemProjectionMap, run_acp

run_acp(
    agent=agent,
    projection_maps=(
        FileSystemProjectionMap(
            default_read_tool="read_file",
            default_write_tool="write_file",
            default_bash_tool="execute",
        ),
    ),
)
```

Hook projection:

```python
from pydantic_acp import HookProjectionMap, run_acp

run_acp(
    agent=agent,
    projection_maps=(
        HookProjectionMap(
            hidden_event_ids=frozenset({"after_model_request"}),
        ),
    ),
)
```

Codex-backed `pydantic-ai` model:

```python
from pydantic_ai import Agent
from codex_auth_helper import create_codex_responses_model

agent = Agent(create_codex_responses_model("gpt-5"))
```

Example entry points:

- `examples/pydantic/static_agent.py`
  smallest direct `run_acp(agent=...)` setup
- `examples/pydantic/hook_projection.py`
  native `Hooks` capability introspection rendered through `HookProjectionMap`
- `examples/pydantic/my_agent.py`
  broad end-to-end ACP demo combining factories, providers, approvals, bridges, and host helpers

## Development

ACP Kit uses `uv` for dependency management and tool execution. The canonical local checks are:

```bash
uv run ruff check
uv run ty check
uv run basedpyright
make tests
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
- `examples/pydantic/README.md`: example inventory and runnable demo entry points
- `docs/helpers.md`: helper packages, including `codex-auth-helper`
- `docs/providers.md`: provider seams
- `docs/bridges.md`: capability bridge system
- `docs/host-backends.md`: filesystem and terminal helpers
- `docs/testing.md`: behavioral test surface

## License

MIT
