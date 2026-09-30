# LangChain ACP Session State And Lifecycle

`langchain-acp` treats session lifecycle as first-class adapter state.

Supported lifecycle operations:

- new session
- load session
- list sessions
- fork session
- resume session
- close session

This is not transport bookkeeping. Session state affects graph rebuilds,
projection behavior, plan state, and config surface.

## ACP 0.12 Session Contract

`langchain-acp` targets ACP Python SDK `0.12.1` and schema v1.19. The adapter uses
`session/set_config_option` for model and mode selection; the removed
`session/set_model` RPC is not sent on the wire. Config options are emitted
only when the client advertises `session.configOptions`, and boolean options
also require `session.configOptions.boolean`.

`AdapterConfig(plan_update_mode="full")` remains the default. Set
`plan_update_mode="content"` only when incremental plan reconciliation is
useful: the adapter emits `plan_update` and `plan_removed` when the client
advertises `plan`, otherwise it safely falls back to the full `plan` update.

## SessionStore

The adapter uses a `SessionStore` abstraction:

- `MemorySessionStore`
- `FileSessionStore`

Use `MemorySessionStore` for tests and disposable processes. Use
`FileSessionStore` when ACP sessions must survive process restarts or should be
inspectable on disk.

## What A Session Carries

Stored session state includes:

- `cwd`
- `additional_directories`
- session-local model id
- session-local mode id
- config values
- plan state

`FileSessionStore` persists those values as local JSON files. File-backed session ids are restricted
to ASCII letters, digits, `_`, and `-`, with a 128-character limit, so a client-supplied session id
cannot escape the configured store root.
- MCP server definitions
- transcript updates
- metadata

That state is represented through `AcpSessionContext` and replayed back into the
runtime when a session is reloaded.

`AcpSessionContext` is the same object the adapter passes to `graph_factory`,
providers, and replay-sensitive runtime seams.

The adapter persists `additional_directories` through new, load, fork, resume,
and list responses so a graph factory can treat declared sibling worktrees as
session input without losing them across reconnects.

## ACP-Transport MCP Descriptors

ACP 0.12 accepts an `AcpMcpServer` descriptor during session creation. The
adapter preserves it in session state and exposes it through `/mcp-servers`,
but does not create a connection or advertise `McpCapabilities.acp`: the ACP
Python SDK has no public MCP router for that transport.

```python
from acp.schema import AcpMcpServer

delegated_agent = AcpMcpServer(
    server_id="workspace-reviewer",
    name="Workspace reviewer",
    type="acp",
)

response = await acp_agent.new_session(
    cwd="/workspace",
    mcp_servers=[delegated_agent],
)
```

Use the descriptor when a host owns the delegated ACP connection. For
tool-executing MCP integrations, use the HTTP, SSE, or stdio forms supported
by the active graph/tool bridge.

## Typed Elicitation

Graph factories and providers can use the typed session context to request
form or URL input from a capable ACP client:

```python
from acp.schema import (
    ElicitationFormSessionMode,
    ElicitationSchema,
)
from langchain_acp import AcpSessionContext


async def request_confirmation(session: AcpSessionContext) -> None:
    mode = ElicitationFormSessionMode(
        session_id=session.session_id,
        requested_schema=ElicitationSchema(),
    )
    await session.create_elicitation("Confirm deployment", mode)
```

The context checks `ClientCapabilities.elicitation` before forwarding the
request and raises an ACP request error when the client or mode is unavailable.

## Transcript Replay

`replay_history_on_load=True` means the adapter replays stored transcript state
into the next graph run instead of treating previous ACP turns as disposable UI
history.

That matters when:

- a graph factory rebuilds a graph from session state
- a session-local model or mode changes over time
- plan state must persist across restarts

## Graph Ownership And Session Rebuilds

LangChain session lifecycle is tied to graph ownership:

- `graph=...` means one static compiled graph
- `graph_factory=session -> graph` means session-aware rebuild
- `graph_source=...` gives you a custom retrieval seam

If session state should change the upstream graph, use `graph_factory=` or a
custom `GraphSource`.

## Example: Durable Session Store

```python
from pathlib import Path

from langchain.agents import create_agent
from langchain_acp import (
    AcpSessionContext,
    AdapterConfig,
    CompiledAgentGraph,
    FileSessionStore,
    run_acp,
)


def graph_from_session(session: AcpSessionContext) -> CompiledAgentGraph:
    model_name = session.session_model_id or "openai:gpt-5-mini"
    return create_agent(model=model_name, tools=[])


config = AdapterConfig(
    session_store=FileSessionStore(root=Path(".acpkit/langchain-sessions")),
    replay_history_on_load=True,
)

run_acp(graph_factory=graph_from_session, config=config)
```

## Fork And Resume Semantics

Forking clones the persisted ACP session state into a new session id and new
`cwd`. Resuming keeps the original session identity and reloads the persisted
state.

Use:

- fork when the user wants a branch
- resume when the user wants continuity

## Common Failure Modes

- using a static graph when ACP session state is supposed to rebuild runtime
  behavior
- persisting transcript state but disabling replay when later turns still depend
  on previous session-local controls
- storing plan state in the host app but forgetting to reflect it through
  `PlanProvider` or native plan persistence
