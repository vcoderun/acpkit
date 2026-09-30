# langchain-acp

`langchain-acp` exposes LangChain, LangGraph, and DeepAgents graphs through ACP Kit.

It keeps ACP Kit's adapter architecture intact while staying graph-centric on the LangChain side:

- `graph=...`
- `graph_factory=...`
- `graph_source=...`

## Install

```bash
uv add langchain-acp
```

```bash
pip install langchain-acp
```

With optional DeepAgents compatibility:

```bash
uv add "langchain-acp[deepagents]"
```

```bash
pip install "langchain-acp[deepagents]"
```

Add `web` for the official ACP 0.12 Streamable HTTP and WebSocket dependencies:

```bash
uv add "langchain-acp[web]"
```

Contributor setup from the monorepo root:

```bash
uv sync --extra dev --extra langchain
```

Public API and deprecation guarantees are documented in the
[versioning policy](https://vcoderun.github.io/acpkit/versioning/).

## Supported Framework Versions

The current compatibility baseline is:

- `langchain>=1.3.11`
- `langgraph>=1.2.7`
- `deepagents>=0.6.12` through the optional `deepagents` extra

These are minimum versions rather than upper pins. The lockfile and CI validate the exact baseline
together, including a real `create_deep_agent(...)` graph adapted through ACP. DeepAgents 0.6 tool
calls such as `read_file(file_path=...)`, `write_file(file_path=...)`, `glob(...)`, `grep(...)`,
`ls(...)`, and `execute(command=...)` are covered by `DeepAgentsProjectionMap`.

## Quickstart

```python
from langchain.agents import create_agent
from langchain_acp import run_acp

graph = create_agent(model="openai:gpt-5", tools=[])

run_acp(graph=graph)
```

## ACP 0.12 Controls

The adapter targets `agent-client-protocol==0.12.1` and ACP schema v1.19. Model and mode changes
use session config options instead of the removed `session/set_model` RPC.
`AdapterConfig(plan_update_mode="content")` sends incremental plan changes to
clients that advertise the `plan` capability and automatically falls back to a
complete plan otherwise.

`additional_directories` persist with the session and typed client input is
available through `AcpSessionContext.create_elicitation(...)`. The adapter
also retains ACP 0.12 `AcpMcpServer` session descriptors for a host-owned
delegated connection, but does not connect them or advertise ACP MCP transport
support because the SDK has no public router. Use HTTP, SSE, or stdio MCP
descriptors for actual graph tool integrations.

If you are using Codex-backed LangChain models through `codex-auth-helper`, you must pass the
LangChain system behavior through the helper's `instructions=` argument. The same repo policy now
applies on the Pydantic path too: Codex-backed model factories take explicit instructions instead of
inventing an implicit default.

```python
from codex_auth_helper import create_codex_chat_openai
from langchain.agents import create_agent

model = create_codex_chat_openai(
    "gpt-5.4",
    instructions="You are a careful assistant that explains concrete workspace observations.",
)
graph = create_agent(model=model, tools=[], name="codex-graph")
```

That `instructions` string is required and is forwarded to the OpenAI Responses request behind
`ChatOpenAI`. See the maintained example at
<https://github.com/vcoderun/acpkit/blob/main/examples/langchain/codex_graph.py>.

Use the same pattern inside `graph_factory=` paths too. If the graph is rebuilt per session, keep
the Codex system behavior explicit in the factory instead of relying on an implicit default:

```python
from codex_auth_helper import create_codex_chat_openai
from langchain.agents import create_agent
from langchain_acp import AcpSessionContext, CompiledAgentGraph


def graph_from_session(session: AcpSessionContext) -> CompiledAgentGraph:
    mode_name = session.session_mode_id or "ask"
    model_name = session.session_model_id or "gpt-5.4-mini"
    model = create_codex_chat_openai(
        model_name,
        instructions=f"Operate in {mode_name} mode and explain concrete workspace observations.",
    )
    return create_agent(model=model, tools=[], name=f"codex-{mode_name}")
```

If ACP session state should affect graph construction, use `graph_factory=`:

```python
from langchain.agents import create_agent
from langchain_acp import AcpSessionContext, CompiledAgentGraph, create_acp_agent


def graph_from_session(session: AcpSessionContext) -> CompiledAgentGraph:
    mode_name = session.session_mode_id or "default"
    return create_agent(model="openai:gpt-5", tools=[], name=f"graph-{mode_name}")


acp_agent = create_acp_agent(graph_factory=graph_from_session)
```

## What The Adapter Covers

`langchain-acp` carries the same ACP Kit seams that matter elsewhere in the repo, but mapped onto graph ownership instead of agent ownership:

- session stores and transcript replay
- model, mode, and config-option providers
- prompt capability advertisement through `prompt_capabilities`
- native plan state through `TaskPlan`
- approval bridging from `HumanInTheLoopMiddleware`
- remembered approval policies and permission card rendering on `NativeApprovalBridge`
- capability bridges and graph-build contributions
- built-in and host-defined slash commands
- tool projection maps and event projection maps
- external hook/event projection through `ExternalHookEventBridge`
- `graph`, `graph_factory`, and `graph_source`
- DeepAgents compatibility helpers where they add truthful ACP behavior

That means the adapter can expose:

- plain LangChain `create_agent(...)` graphs
- compiled LangGraph graphs
- DeepAgents graphs

without collapsing everything into a bespoke ACP runtime.

## Runtime Controls

The adapter now owns a small ACP-native slash-command layer instead of leaving that surface entirely to the graph:

- mode commands such as `/ask` or `/review` when the session publishes modes
- `/model` for ACP-owned model selection
- `/tools` for the active graph tool node
- `/mcp-servers` for attached session MCP servers
- custom host commands through `slash_command_provider`

Example:

```python
from acp.schema import AvailableCommand
from langchain_acp import (
    AdapterConfig,
    SlashCommandResult,
    StaticSlashCommand,
    StaticSlashCommandProvider,
)

config = AdapterConfig(
    slash_command_provider=StaticSlashCommandProvider(
        commands=[
            StaticSlashCommand(
                command=AvailableCommand(name="ping", description="Return pong."),
                handler=lambda _request: SlashCommandResult(text="pong"),
            )
        ]
    )
)
```

Prompt capability advertisement is also explicit now:

```python
from langchain_acp import AdapterConfig, AdapterPromptCapabilities

config = AdapterConfig(
    prompt_capabilities=AdapterPromptCapabilities(
        audio=False,
        image=False,
        embedded_context=True,
    )
)
```

If the graph uses approval middleware, remembered choices and ACP permission presentation stay on
`NativeApprovalBridge`, not on `AdapterConfig`:

```python
from langchain_acp import NativeApprovalBridge

config = AdapterConfig(
    approval_bridge=NativeApprovalBridge(enable_persistent_choices=True),
)
```

## Session-owned Graph Rebuilds

If ACP session state should decide which graph gets built, `graph_factory=` is the intended seam:

```python
from langchain.agents import create_agent
from langchain_acp import (
    AcpSessionContext,
    AdapterConfig,
    CompiledAgentGraph,
    MemorySessionStore,
    run_acp,
)


def graph_from_session(session: AcpSessionContext) -> CompiledAgentGraph:
    mode_name = session.session_mode_id or "default"
    model_name = session.session_model_id or "openai:gpt-5-mini"
    return create_agent(
        model=model_name,
        tools=[],
        name=f"graph-{mode_name}",
        system_prompt=f"Operate in {mode_name} mode.",
    )


run_acp(
    graph_factory=graph_from_session,
    config=AdapterConfig(session_store=MemorySessionStore()),
)
```

Use this when workspace path, mode, model, or session metadata should rebuild the graph dynamically.

The maintained examples under `examples/langchain/` also expose ACP-visible model and mode choices
through `available_models`, `available_modes`, `default_model_id`, and `default_mode_id`, then
consume `session.session_model_id` and `session.session_mode_id` inside `graph_factory=...`.

## Session Store Notes

Use `MemorySessionStore` for ephemeral graph sessions and `FileSessionStore` when ACP session state
should survive process restarts. The file store persists the ACP transcript, selected model, selected
mode, config values, and plan state as local JSON.

File-backed session ids are constrained before they become filenames:

- allowed characters are ASCII letters, digits, `_`, and `-`
- maximum length is 128 characters
- path separators, dot-prefixed ids, whitespace, and shell metacharacters are rejected

`FileSessionStore` is a local durable store, not a distributed database. Do not share the same store
root between unrelated users or untrusted processes.

## DeepAgents Compatibility

DeepAgents graphs are supported as compiled LangGraph targets.

On DeepAgents 0.6, filesystem state uses its newer LangGraph state channels and tool results may be
structured `ToolMessage` values. `langchain-acp` deliberately treats graph state as upstream-owned
and projects the stable tool-call contract instead of depending on DeepAgents internals.

Use the compatibility helpers only when they add real value:

- `DeepAgentsCompatibilityBridge`
- `DeepAgentsProjectionMap`

Maintained examples:

- [workspace_graph.py](https://github.com/vcoderun/acpkit/blob/main/examples/langchain/workspace_graph.py)
- [deepagents_graph.py](https://github.com/vcoderun/acpkit/blob/main/examples/langchain/deepagents_graph.py)

Docs:

- <https://vcoderun.github.io/acpkit/langchain-acp/>
- <https://vcoderun.github.io/acpkit/getting-started/langchain-quickstart/>
- <https://vcoderun.github.io/acpkit/examples/langchain-workspace/>
- <https://vcoderun.github.io/acpkit/examples/deepagents/>
- <https://vcoderun.github.io/acpkit/security/>
