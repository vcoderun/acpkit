# LangChain ACP Bridges

Capability bridges are the main extension seam in `langchain-acp`.

They let the adapter expose ACP-visible behavior without hard-coding every
product policy into the runtime core.

## Built-in Bridges

The built-in bridge set is:

- `ModelSelectionBridge`
- `ModeSelectionBridge`
- `ConfigOptionsBridge`
- `ToolSurfaceBridge`
- `DeepAgentsCompatibilityBridge`

## What Bridges Can Influence

On the LangChain side, bridges contribute through graph build aggregation rather
than through Pydantic capability objects.

That means bridges can influence:

- model and mode state
- config options
- tool classification
- approval policy keys
- session metadata
- plan extraction
- graph build contributions

## Graph Build Contributions

`GraphBridgeBuilder` and `GraphBuildContributions` aggregate bridge output into
one graph-shaped contribution object.

That contribution seam can affect:

- middleware
- tools
- system prompt parts
- response format
- interrupt configuration
- graph metadata

This is the main reason `langchain-acp` does not collapse into one monolithic
runtime file.

### Example: Build A Bridge Stack

```python
from langchain_acp import (
    AdapterConfig,
    ConfigOptionsBridge,
    DeepAgentsCompatibilityBridge,
    GraphBridgeBuilder,
    ToolSurfaceBridge,
)

config = AdapterConfig(
    capability_bridges=[
        ConfigOptionsBridge(),
        ToolSurfaceBridge(
            tool_kinds={
                "read_file": "read",
                "execute": "execute",
            },
            approval_policy_keys={
                "execute": "shell",
            },
        ),
        DeepAgentsCompatibilityBridge(),
    ]
)

builder = GraphBridgeBuilder.from_config(config)
```

That is the normal shape when the runtime owns a real graph but ACP should see
more than the raw upstream callbacks.

## Example: Tool Classification

```python
from langchain_acp import AdapterConfig, ToolSurfaceBridge

config = AdapterConfig(
    capability_bridges=[
        ToolSurfaceBridge(
            tool_kinds={
                "read_file": "read",
                "execute": "execute",
            },
            approval_policy_keys={
                "execute": "shell",
            },
        )
    ]
)
```

## DeepAgents Compatibility Bridge

`DeepAgentsCompatibilityBridge` is intentionally narrow.

Use it when you want:

- DeepAgents-flavored session metadata
- `write_todos` plan extraction
- predictable projection defaults for the DeepAgents example runtime

Do not use it as a general-purpose replacement for native ACP plan ownership.

## When To Write A Custom Bridge

Write one when:

- the host already has product policy that should stay outside the adapter core
- ACP metadata should expose a runtime concern the graph already owns
- tool classification needs product-specific semantics

Do not write one just to repackage static config that `AdapterConfig` can
already represent directly.
