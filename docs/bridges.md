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
- `McpBridge` only contributes MCP metadata and classification; it does not register the underlying tools for you

## Existing Hook Introspection vs HookBridge

These are related but not identical:

- **existing hook introspection**
  observes a `Hooks` capability that was already present on the source agent
- **`HookBridge`**
  contributes a bridge-owned `Hooks` capability during the session build

If you want to render existing hook callbacks, use `HookProjectionMap`.
If you want the bridge layer to contribute hooks itself, use `HookBridge`.
