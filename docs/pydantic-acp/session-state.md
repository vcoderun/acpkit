# Session State And Lifecycle

ACP Kit treats session state as a first-class contract.

Each session carries the information needed to:

- replay ACP transcript history
- resume the current workspace and config state
- keep plan state stable across prompts
- reflect mode, model, and approval metadata accurately

## What Is Stored

An `AcpSessionContext` captures:

- `session_id`
- `cwd`
- `created_at` and `updated_at`
- session-local `config_values`
- `session_model_id`
- ACP transcript updates
- serialized message history
- `plan_entries` and `plan_markdown`
- MCP server metadata
- adapter-owned session metadata

## Session Lifecycle Operations

`pydantic-acp` supports the full ACP session lifecycle:

- create
- load
- list
- fork
- resume
- close

When a stored session is loaded or resumed, the adapter can replay transcript and history state so the client sees a consistent session surface.

## Session Stores

### MemorySessionStore

Use `MemorySessionStore` when process-local state is enough:

```python
from pydantic_acp import AdapterConfig, MemorySessionStore

config = AdapterConfig(session_store=MemorySessionStore())
```

### FileSessionStore

Use `FileSessionStore` when sessions should survive restarts:

```python
from pathlib import Path

from pydantic_acp import AdapterConfig, FileSessionStore

config = AdapterConfig(
    session_store=FileSessionStore(root=Path(".acp-sessions")),
)
```

This is the recommended default for local tools and editor integrations.

## Transcript Replay And History Replay

The adapter stores two related but different views of a run:

- **ACP transcript updates**
  what the ACP client saw
- **message history**
  what the underlying Pydantic AI run should receive on the next turn

That split matters because ACP rendering and model message history are not the same thing.

`replay_history_on_load=True` keeps these aligned across session reloads.

## Cancellation

`cancel(session_id)` is implemented as a real runtime cancellation path, not a no-op.

When a prompt is cancelled:

- the active task is cancelled
- the session history remains well-formed
- the transcript gets a final user-visible cancellation note
- the prompt result reports `stop_reason="cancelled"`

This keeps “Stop” behavior compatible with long-running tool calls, plan workflows, and approval flows.

## Plan Persistence

Native ACP plan state lives on the session:

- `plan_entries`
- `plan_markdown`

If you configure `native_plan_persistence_provider`, each plan update can also be mirrored to a host-owned storage destination such as a workspace file.

## How Session State Interacts With Factories

When you use `agent_factory` or `AgentSource`, the adapter passes the current `AcpSessionContext` into the build path.

That lets you build session-aware agents such as:

- workspace agents keyed to `session.cwd`
- agents whose default model changes by workspace
- tools that read from the bound ACP client and active session id

## Example: File-backed Session State

```python
from pathlib import Path

from pydantic_ai import Agent
from pydantic_acp import AdapterConfig, FileSessionStore, run_acp

agent = Agent("openai:gpt-5", name="persistent-agent")

run_acp(
    agent=agent,
    config=AdapterConfig(
        session_store=FileSessionStore(root=Path(".acp-sessions")),
        replay_history_on_load=True,
    ),
)
```

Use this pattern whenever you want ACP sessions to behave like durable workspaces rather than ephemeral chats.
