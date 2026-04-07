# Bridges

Capability bridges enrich ACP exposure and runtime behavior without forcing the adapter core to
depend on one product-specific model.

Use bridges when ACP-visible state or runtime behavior should be contributed by a separate layer,
not hard-coded into the adapter core.

## Base Types

- `CapabilityBridge`: extension seam for ACP-facing state and projection decisions
- `BufferedCapabilityBridge`: helper base for bridges that emit buffered ACP updates

## Built-In Bridges

### HookBridge

Provides an explicit `Hooks` capability contribution for bridge-builder and factory-owned setups.

Used for:

- run lifecycle events
- node lifecycle events
- tool validation and tool execution events
- prepare-tools lifecycle events

`HookBridge` is not the only hook-related surface in the adapter. The runtime can also observe an
agent's already-registered `Hooks` capability and render those updates through
`HookProjectionMap`.

### PrepareToolsBridge

Shapes tool availability and tool exposure per mode.

Used for:

- ACP mode-aware tool exposure
- runtime prepare-tools integration

### HistoryProcessorBridge

Wraps plain and contextual history processors so their activity can be projected into ACP updates.

### McpBridge

Adds MCP-aware classification and metadata:

- MCP server definitions
- MCP tool definitions
- MCP capability exposure
- approval-policy routing for MCP-scoped tools
- optional config options

## Hook Projection

`HookProjectionMap` controls how observed hook events are rendered into ACP tool-call updates.

It can customize:

- human-readable event labels
- ACP `kind` values per event
- hidden event ids
- whether raw input, raw output, and tool filters are shown
- title formatting, including whether the tool name appears in the title

This is the rendering layer for existing hook callbacks. It does not create or execute the hook
capability by itself.

`HookProjectionMap` can be passed through `projection_maps`:

```python
from pydantic_acp import HookProjectionMap, run_acp

run_acp(
    agent=agent,
    projection_maps=(
        HookProjectionMap(
            hidden_event_ids=frozenset({"after_model_request"}),
            event_labels={"before_tool_execute": "Starting Tool"},
        ),
    ),
)
```

Runnable example:

- `examples/pydantic/hook_projection.py`

## Bridge Builder

`AgentBridgeBuilder` wires bridge-provided capabilities and history processor wrappers into a session-specific agent build.

Typical usage:

```python
from pydantic_acp import AgentBridgeBuilder, HookBridge

builder = AgentBridgeBuilder(
    session=session,
    capability_bridges=[HookBridge()],
)
contributions = builder.build()
```

`AgentBridgeContributions` returns:

- `capabilities`
- `history_processors`

This is the intended path when factories need session-scoped bridge wiring.

## Existing Hook Introspection

When the supplied agent already has a `pydantic_ai.capabilities.Hooks` capability, `pydantic-acp`
can observe that capability directly.

That path powers:

- ACP hook updates during prompt execution
- `/hooks` listing for the active agent
- `HookProjectionMap` rendering of those observed events
