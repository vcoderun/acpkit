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
- session-local model id
- session-local mode id
- config values
- plan state
- MCP server definitions
- transcript updates
- metadata

That state is represented through `AcpSessionContext` and replayed back into the
runtime when a session is reloaded.

`AcpSessionContext` is the same object the adapter passes to `graph_factory`,
providers, and replay-sensitive runtime seams.

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
from langchain_acp import AdapterConfig, FileSessionStore, run_acp


def graph_from_session(session):
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
