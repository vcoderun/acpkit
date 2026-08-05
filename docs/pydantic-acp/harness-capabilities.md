## Harness-backed Capabilities

`pydantic-acp` can expose `pydantic-ai-harness` capability tools through ACP without
rewriting your underlying `pydantic_ai.Agent`.

This is the supported path when you want a Pydantic agent to use:

- workspace-scoped filesystem tools
- bounded shell execution
- optional CodeMode execution tools

ACP Kit validates its maintained harness bridge surface against
`pydantic-ai-harness[code-mode]==0.15.0`. The bridges intentionally use the
public `FileSystem`, `Shell`, and `CodeMode` imports; newer Harness capabilities
such as Memory and Guardrails remain available to the underlying agent without
being reimplemented as ACP Kit-specific tool bridges.

Harness 0.15.0 itself requires `pydantic-ai-slim>=2.22.0`. The core
`pydantic-acp` adapter remains compatible with Pydantic AI 2.9.0 through 2.23.0;
install the `harness` extra only when the resolved Pydantic AI version is in the
Harness-supported part of that range.

The adapter surface is split in two parts:

- bridges add real upstream tool capability to the Pydantic runtime
- projection maps turn those tool calls into cleaner ACP-visible transcript updates

Public harness seams:

- `HarnessFileSystemBridge`
- `HarnessShellBridge`
- `HarnessCodeModeBridge`
- `HarnessFileSystemProjectionMap`
- `HarnessShellProjectionMap`
- `HarnessCodeModeProjectionMap`

Source references:

- [Example agent](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/mock_harness_agent.py)
- [Package README](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/pydantic-acp/README.md)

## Install

Production install with harness support:

```bash
uv add "pydantic-acp[harness]"
```

```bash
pip install "pydantic-acp[harness]"
```

If you also want CodeMode tools:

```bash
uv add "pydantic-ai-harness[code-mode]"
```

```bash
pip install "pydantic-ai-harness[code-mode]"
```

## Minimal Setup

This shape exposes filesystem and shell tools only:

```python
from pathlib import Path

from pydantic_ai import Agent
from pydantic_acp import (
    AdapterConfig,
    HarnessFileSystemBridge,
    HarnessShellBridge,
    MemorySessionStore,
    run_acp,
)

workspace_root = Path("agent_demos/harness-agent")

agent = Agent(
    "openai:gpt-5",
    name="harness-agent",
    instructions="Use filesystem and shell tools inside the workspace only.",
)

run_acp(
    agent=agent,
    config=AdapterConfig(
        session_store=MemorySessionStore(),
        capability_bridges=[
            HarnessFileSystemBridge(root_dir=workspace_root),
            HarnessShellBridge(cwd=workspace_root),
        ],
    ),
)
```

Use `agent_factory=` instead of a single shared `Agent(...)` when the harness workspace,
instructions, or enabled capability set should vary by ACP session.

## CodeMode Should Usually Stay Opt-in

`HarnessCodeModeBridge` adds a much stronger execution surface than plain file and shell tools.
Keep it off by default unless the run explicitly needs it.

The maintained example follows that rule:

- `acp_agent` exposes filesystem and shell only
- `python -m examples.pydantic.mock_harness_agent --codemode` adds `HarnessCodeModeBridge`

That means ACP clients do not see CodeMode unless you intentionally start the example in that mode.

## Projection Behavior

Bridges make the tools callable. Projection maps decide how ACP clients see their activity.

Recommended harness projection stack:

```python
from pydantic_acp import (
    AdapterConfig,
    HarnessCodeModeProjectionMap,
    HarnessFileSystemProjectionMap,
    HarnessShellProjectionMap,
    run_acp,
)

run_acp(
    agent=agent,
    config=AdapterConfig(
        projection_maps=[
            HarnessFileSystemProjectionMap(),
            HarnessShellProjectionMap(),
            HarnessCodeModeProjectionMap(),
        ],
    ),
)
```

Current harness-specific behavior:

- `read_file` renders a read-specific card and a numbered text preview instead of pretending the
  read was a diff
- `write_file` and `edit_file` render write-oriented updates
- `list_directory` and search-style tools render compact workspace inspection summaries
- shell tools render command execution status and bounded output previews
- CodeMode tools render execution-oriented cards instead of raw tool payloads

If you omit the harness projection maps, the tools still work, but ACP transcript rendering is less
intentional.

## A Session-aware Pattern

Use a factory when the workspace root or instructions should be derived from ACP session state:

```python
from pathlib import Path

from pydantic_ai import Agent
from pydantic_acp import AcpSessionContext, AdapterConfig, MemorySessionStore, run_acp


def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    workspace_root = Path(".workspaces") / session.session_id
    return Agent(
        "openai:gpt-5",
        name="workspace-agent",
        instructions=f"Work inside {workspace_root.name} only.",
    )


run_acp(
    agent_factory=build_agent,
    config=AdapterConfig(session_store=MemorySessionStore()),
)
```

Pair that with session-specific harness bridges when the capability surface should follow the same
workspace boundary.

## Maintained Example

The maintained runnable example is:

- [mock_harness_agent.py](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/mock_harness_agent.py)

It demonstrates:

- real `pydantic-ai-harness` bridges instead of mock tools
- default filesystem and shell capability exposure
- opt-in CodeMode via `--codemode`
- provider-backed model override with `ACP_HARNESS_MODEL`
- Codex-backed model construction with `ACP_HARNESS_CODEX_MODEL`

Native ACP target:

```bash
uv run acpkit run examples.pydantic.mock_harness_agent:acp_agent
```

CodeMode run:

```bash
uv run python -m examples.pydantic.mock_harness_agent --codemode
```

## Practical Guardrails

Good defaults for harness-backed agents:

- set a narrow `root_dir` for filesystem access
- deny obviously dangerous shell commands up front
- keep shell output capped
- leave `persist_cwd=False` unless session-local directory drift is required
- make CodeMode opt-in instead of always-on
- use projection maps so ACP clients see readable tool activity rather than raw payloads
