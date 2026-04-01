# Bridges

Capability bridges enrich ACP exposure and runtime behavior without forcing the adapter core to depend on one product-specific model.

## Base Types

- `CapabilityBridge`: extension seam for ACP-facing state and projection decisions
- `BufferedCapabilityBridge`: helper base for bridges that emit buffered ACP updates

## Built-In Bridges

### HookBridge

Maps Pydantic AI lifecycle activity into ACP-visible tool-call style updates.

Used for:

- run lifecycle events
- node lifecycle events
- tool validation and tool execution events
- prepare-tools lifecycle events

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
