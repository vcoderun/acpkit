# Session State And Lifecycle

ACP Kit treats session state as a first-class contract.

Each session carries the information needed to:

- replay ACP transcript history
- resume the current workspace and config state
- keep plan state stable across prompts
- reflect mode, model, and approval metadata accurately

## ACP 0.12 Session Contract

`pydantic-acp` targets ACP Python SDK `0.12.1` and schema v1.19. Model selection is a
session config option, not a `session/set_model` RPC. Configure a concrete
model only when the adapter exposes a selectable `"model"` option; otherwise
keep the agent default with `AcpProvider.model()` or `create_acp_model(...)`
without `model_name`.

Client capability negotiation is respected:

- no `session.configOptions` capability means no config option surface is sent
- select options require `session.configOptions`
- boolean options additionally require `session.configOptions.boolean`
- `plan_update_mode="content"` uses `plan_update` and `plan_removed` only
  when the client advertises `plan`; it otherwise falls back to full `plan`
  updates

This preserves a usable full-plan surface for older clients while allowing
newer clients to reconcile a named plan incrementally.

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
- `additional_directories`

ACP client-supplied MCP servers are stored in `session.mcp_servers` so load, fork, resume, and
`/mcp-servers` can reflect the same session surface. They become runnable Pydantic AI MCP tools
only when the agent build includes `SessionMcpBridge`.

ACP 0.12 also permits an ACP-transport descriptor. It is preserved across the
session lifecycle so the hosting application can retain its identity and
metadata, but `pydantic-acp` does not connect it or advertise
`McpCapabilities.acp`: the SDK does not expose a public ACP MCP router yet.

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

Use HTTP, SSE, or stdio descriptors with `SessionMcpBridge` when the Pydantic
agent must actually invoke MCP tools. Use `AcpMcpServer` only to preserve a
delegated ACP endpoint for a host that owns the connection.

`additional_directories` is persisted through new, load, fork, resume, and
list operations. `ClientHostContext` treats those directories as extra session
roots for host-backed file and terminal requests. An explicit
`workspace_root` remains a hard boundary, so a client cannot use an additional
directory to escape host policy.

## Typed Elicitation

Session-aware agent factories and providers can ask the connected client for
input through `AcpSessionContext`. Build the exact ACP mode object and let the
context reject unsupported client capabilities before any request is sent:

```python
from acp.schema import (
    ElicitationFormSessionMode,
    ElicitationSchema,
)
from pydantic_acp import AcpSessionContext


async def request_confirmation(session: AcpSessionContext) -> None:
    mode = ElicitationFormSessionMode(
        session_id=session.session_id,
        requested_schema=ElicitationSchema(),
    )
    await session.create_elicitation("Confirm deployment", mode)
```

`create_elicitation(...)` requires the matching `ClientCapabilities.elicitation`
mode and a connected ACP client. It fails explicitly rather than fabricating a
host UI.

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

`FileSessionStore` is designed as a durable local-host store, not a distributed coordination layer.

Current behavior:

- writes use a temp file, `fsync`, and atomic replace
- session ids are restricted to ASCII letters, digits, `_`, and `-`, with a 128-character limit
- the store takes a process-local lock and a filesystem advisory lock when available
- malformed or partially-written session files are skipped by public load/list flows instead of crashing the whole operation
- stale temp files from interrupted writes are cleaned up on startup

That makes it appropriate for:

- editor integrations
- local desktop agents
- single-host ACP services

It is not a substitute for a real multi-writer shared backend.

## Recovery Guarantees Versus Recovery Metrics

`pydantic-acp` does not publish a built-in "session recovery success rate" metric for
`FileSessionStore`.

What the adapter does guarantee is the recovery behavior:

- valid saved sessions can be loaded, listed, resumed, and forked after restart
- malformed saved files are skipped by public load/list flows instead of crashing the store
- interrupted temp-file writes are cleaned up on the next store startup

If your product needs an operational success-rate number, treat that as host-owned monitoring.
For example, measure:

- successful `load_session` or `resume_session` calls after restart
- skipped malformed session files
- file permission or disk errors around the session root

ACP Kit gives you the durability and recovery semantics; SLO-style recovery percentages belong in
your deployment telemetry.

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

If you also want native ACP plans mirrored into workspace-owned storage, pair this with
`native_plan_persistence_provider` from the plan workflow docs.
