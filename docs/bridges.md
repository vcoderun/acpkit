# Bridges

Capability bridges are the adapter’s main extension seam for ACP-visible runtime behavior.

Use a bridge when you want to contribute:

- ACP session metadata
- config options
- modes
- MCP server classification
- buffered ACP updates
- model settings derived from session state
- Pydantic AI capabilities that should be wired into the active agent

## Base Types

| Type | Purpose |
|---|---|
| `CapabilityBridge` | synchronous or async hook point for ACP-facing state and agent contributions |
| `BufferedCapabilityBridge` | base class for bridges that emit buffered ACP update objects |

## Writing A Custom `CapabilityBridge`

Most docs show how to configure built-in bridges. If you are extending the SDK, the key thing to understand is that a bridge is just a narrow, synchronous contribution surface that the runtime polls at specific points.

Use plain `CapabilityBridge` when you only need to:

- add session metadata
- classify tools
- expose MCP transport capability flags
- expose config options or mode state
- derive model settings from session state

Use `BufferedCapabilityBridge` when the bridge also needs to emit ACP transcript updates over time.

### Override Matrix

| Method | Override it when | Return value |
|---|---|---|
| `get_session_metadata(...)` | you want a metadata section under your bridge `metadata_key` | `dict[str, JsonValue]` |
| `get_tool_kind(...)` | you want custom ACP tool classification | `ToolKind` |
| `get_mcp_capabilities(...)` | your bridge requires MCP transport capability flags | `McpCapabilities` |
| `get_config_options(...)` / `set_config_option(...)` | the bridge owns ACP config surface | `list[ConfigOption]` |
| `get_mode_state(...)` / `set_mode(...)` | the bridge owns ACP-visible mode state | `ModeState` |
| `get_model_settings(...)` | session state should change model settings | `ModelSettings` |
| `drain_updates(...)` | the bridge emits buffered ACP transcript updates | `list[SessionTranscriptUpdate]` |

Practical rules:

- set `metadata_key` if you want your metadata to appear in session metadata
- keep classification deterministic; the first bridge that returns a `ToolKind` wins
- return `None` when your bridge is not authoritative for that surface
- use bridge-local buffering only when you truly need ACP transcript updates, not just metadata

## Compatibility Note: History Processor Types

`HistoryProcessorBridge` depends on Pydantic AI history-processor callable types.
ACP Kit models those callable shapes locally and passes them through the public
`Agent(..., history_processors=...)` interface.

That means:

- bridge extension code should import history-processor aliases from
  `pydantic_acp`, not from `pydantic_ai._history_processor`
- the adapter is no longer directly coupled to upstream private
  history-processor imports

## Example: Custom Hook Introspection + MCP Metadata Classification

This is the missing pattern most custom integrations need: inspect hooks already attached to the source agent, expose them in ACP metadata, and classify a subset of tools as MCP-backed search or execute tools.

```python
from dataclasses import dataclass

from acp.schema import McpCapabilities, ToolKind
from pydantic_ai import Agent
from pydantic_acp import (
    AcpSessionContext,
    AdapterConfig,
    CapabilityBridge,
    JsonValue,
    RegisteredHookInfo,
    RuntimeAgent,
    list_agent_hooks,
    run_acp,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class HookAwareMcpBridge(CapabilityBridge):
    metadata_key: str | None = "workspace"
    search_prefix: str = "mcp_repo_"
    execute_prefix: str = "mcp_shell_"

    def get_session_metadata(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> dict[str, JsonValue]:
        hook_infos = list_agent_hooks(agent)
        return {
            "cwd": str(session.cwd),
            "hook_count": len(hook_infos),
            "hooks": [self._serialize_hook_info(hook_info) for hook_info in hook_infos],
        }

    def get_mcp_capabilities(self, agent: RuntimeAgent | None = None) -> McpCapabilities:
        del agent
        return McpCapabilities(http=True)

    def get_tool_kind(self, tool_name: str, raw_input: JsonValue | None = None) -> ToolKind | None:
        del raw_input
        if tool_name.startswith(self.search_prefix):
            return "search"
        if tool_name.startswith(self.execute_prefix):
            return "execute"
        return None

    def _serialize_hook_info(self, hook_info: RegisteredHookInfo) -> JsonValue:
        return {
            "event_id": hook_info.event_id,
            "hook_name": hook_info.hook_name,
            "tool_filters": list(hook_info.tool_filters),
        }


agent = Agent("openai:gpt-5", name="hook-aware-agent")

run_acp(
    agent=agent,
    config=AdapterConfig(
        capability_bridges=[HookAwareMcpBridge()],
    ),
)
```

What this bridge is doing:

- `list_agent_hooks(agent)` introspects hooks that were already attached to the source agent
- `metadata_key = "workspace"` makes the returned metadata appear under `session.metadata["workspace"]`
- `get_mcp_capabilities(...)` advertises that the bridge contributes MCP-aware HTTP metadata
- `get_tool_kind(...)` classifies matching tools before the base tool classifier runs

When to promote this to `BufferedCapabilityBridge`:

- you want ACP transcript cards when the bridge itself completes work
- you need `_record_completed_event(...)` or `_record_failed_event(...)`
- metadata alone is not enough; the client should see a time-ordered update stream

## Existing Hook Introspection Helpers

The bridge example above depends on one public helper:

- `list_agent_hooks(agent)`

Use it when you want to inspect hooks that already exist on the source agent.

That is different from `HookBridge`:

- **existing hook introspection**
  inspects hooks that are already present on the source agent
- **`HookBridge`**
  contributes bridge-owned hook capability at build time

If you want to render existing hook callbacks in session metadata or ACP listings, start with `list_agent_hooks(...)`.
If you want the bridge layer itself to contribute hook behavior, use `HookBridge`.

## Event Stream Hook Contract

Pydantic AI treats `run_event_stream` differently from the ordinary async hook callbacks.

The contract is:

- `run_event_stream` must return an `AsyncIterable[AgentStreamEvent]`
- it must not return a coroutine that later resolves to a stream
- if you instrument or wrap that hook, preserve the async-iterable boundary

This matters for both custom `Hooks(...)` usage and hook introspection wrappers.
If you accidentally return a coroutine, the run will fail when the runtime reaches `async for`.

## Built-in Bridges

### `PrepareToolsBridge`

Shapes tool availability per mode.

Use it for:

- read-only vs write-enabled modes
- hiding dangerous tools in planning mode
- activating native ACP plan state
- exposing plan progress tools only in execution modes

It is the bridge most real coding-agent setups start with.

### `ThinkingBridge`

Exposes Pydantic AI’s `Thinking` capability through ACP session config.

Use it when:

- you want a session-local reasoning effort selector
- ACP clients should be able to inspect or change thinking effort

### `HookBridge`

Adds a `Hooks` capability into the active agent.

Useful when you want ACP-visible hook updates that come from bridge-owned hooks rather than only from hooks already attached to the source agent.

You can also suppress noisy default hook rendering with:

```python
HookBridge(hide_all=True)
```

### `HistoryProcessorBridge`

Wraps history processors so their activity can be reflected into ACP updates.

This is useful when you want message-history trimming or contextual rewriting to remain observable.

### `McpBridge`

Adds MCP-aware metadata and tool classification:

- server definitions
- tool-to-server mapping
- tool kind classification
- approval-policy key routing
- optional config surface for MCP-backed state

## Example: Mode-aware Tools + MCP Metadata + Thinking

```python
from pydantic_acp import (
    AdapterConfig,
    McpBridge,
    McpServerDefinition,
    McpToolDefinition,
    PrepareToolsBridge,
    PrepareToolsMode,
    ThinkingBridge,
)
from pydantic_ai.tools import RunContext, ToolDefinition


def ask_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return [tool_def for tool_def in tool_defs if tool_def.name == "mcp_repo_search_paths"]


def agent_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return list(tool_defs)


config = AdapterConfig(
    capability_bridges=[
        ThinkingBridge(),
        PrepareToolsBridge(
            default_mode_id="ask",
            modes=[
                PrepareToolsMode(
                    id="ask",
                    name="Ask",
                    description="Read-only inspection mode.",
                    prepare_func=ask_tools,
                ),
                PrepareToolsMode(
                    id="agent",
                    name="Agent",
                    description="Full workspace mode.",
                    prepare_func=agent_tools,
                    plan_tools=True,
                ),
            ],
        ),
        McpBridge(
            servers=[
                McpServerDefinition(
                    server_id="repo",
                    name="Repository",
                    transport="http",
                    tool_prefix="mcp_repo_",
                    description="Repository inspection tools.",
                )
            ],
            tools=[
                McpToolDefinition(
                    tool_name="mcp_repo_search_paths",
                    server_id="repo",
                    kind="search",
                )
            ],
        ),
    ],
)
```

## Bridge Builder

`AgentBridgeBuilder` is the intended way to assemble bridge contributions into a session-specific agent build:

```python
from pydantic_acp import AgentBridgeBuilder

builder = AgentBridgeBuilder(
    session=session,
    capability_bridges=bridges,
)
contributions = builder.build()
```

It returns:

- `capabilities`
- `history_processors`

That makes it a natural fit inside `agent_factory` or `AgentSource.get_agent(...)`.

## Common Failure Modes

- defining multiple `PrepareToolsMode(..., plan_mode=True)` entries raises an error; native plan mode is singular
- using reserved mode ids such as `model`, `thinking`, `tools`, `hooks`, or `mcp-servers` raises an error because those names are reserved for slash commands
- `HookBridge(hide_all=True)` hides hook listing output; it does not remove hook capability wiring
- `run_event_stream` wrappers must return an async iterable; returning a coroutine or plain object breaks stream execution
- `McpBridge` only contributes MCP metadata and classification; it does not register the underlying tools for you

## Existing Hook Introspection vs HookBridge

These are related but not identical:

- **existing hook introspection**
  observes a `Hooks` capability that was already present on the source agent
- **`HookBridge`**
  contributes a bridge-owned `Hooks` capability during the session build

If you want to render existing hook callbacks, use `HookProjectionMap`.
If you want the bridge layer to contribute hooks itself, use `HookBridge`.
