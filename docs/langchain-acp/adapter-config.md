# LangChain ACP AdapterConfig

`AdapterConfig` is the main configuration object for `langchain-acp`.

It controls four things:

1. ACP identity
2. session-owned runtime state
3. plan and approval behavior
4. projection and bridge wiring

Unlike `pydantic-acp`, this config is graph-oriented. The adapter does not patch
`pydantic_ai.Agent` internals or attach upstream Pydantic capabilities. It shapes
how ACP state maps onto a compiled LangGraph or LangChain graph.

## Identity

These fields set the ACP-facing identity:

- `agent_name`
- `agent_title`
- `agent_version`

Use them when the ACP client should see a product-specific name instead of the
package defaults.

## Model, Mode, And Config Surface

Built-in state:

- `available_models`
- `available_modes`
- `default_model_id`
- `default_mode_id`

Provider-owned state:

- `models_provider`
- `modes_provider`
- `config_options_provider`

Use built-in lists when the adapter itself can own the state cleanly. Use
providers when the host application already stores the state and ACP should
reflect it instead of becoming the source of truth.

## Plans And Approvals

Plan-related fields:

- `plan_mode_id`
- `default_plan_generation_type`
- `enable_plan_progress_tools`
- `plan_provider`
- `native_plan_persistence_provider`
- `native_plan_additional_instructions`

Approval-related field:

- `approval_bridge`

This split is deliberate:

- plans are adapter-owned or provider-owned ACP state
- approvals map runtime pauses into ACP permission requests

## Projection And Event Wiring

Tool and event shaping happens here:

- `projection_maps`
- `event_projection_maps`
- `tool_classifier`
- `capability_bridges`

Use these when the raw graph runtime is correct but ACP rendering needs better
tool classification, filesystem diffs, shell previews, or event projection.

The adapter keeps these two channels separate on purpose:

- tool projection maps summarize deliberate tool calls
- event projection maps summarize callback or trace payloads that are not tool
  calls

If a runtime already emits `AgentMessageChunk` or `ToolCallProgress`-style
events, project those events explicitly instead of flattening them into generic
text.

## Persistence And Replay

Session durability is controlled by:

- `session_store`
- `replay_history_on_load`

Supported stores:

- `MemorySessionStore`
- `FileSessionStore`

Replay matters more in `langchain-acp` than in a throwaway transport because
session-local model, mode, plan, and transcript state often drive graph rebuilds.

When `graph_factory=` depends on `AcpSessionContext`, replay is what keeps the
next turn aligned with the last persisted session state.

## Output Serialization

`output_serializer` controls how raw tool and event payloads are converted into
ACP-visible text when no richer projection exists.

Most integrations can keep the default serializer and only customize
`projection_maps` or `event_projection_maps`.

## Minimal Example

```python
from acp.schema import ModelInfo, SessionMode
from langchain_acp import (
    AdapterConfig,
    DeepAgentsCompatibilityBridge,
    DeepAgentsProjectionMap,
    FileSessionStore,
    StructuredEventProjectionMap,
)

config = AdapterConfig(
    agent_name="workspace-graph",
    available_models=[
        ModelInfo(model_id="fast", name="Fast"),
        ModelInfo(model_id="deep", name="Deep"),
    ],
    available_modes=[
        SessionMode(id="ask", name="Ask"),
        SessionMode(id="agent", name="Agent"),
    ],
    default_model_id="fast",
    default_mode_id="ask",
    session_store=FileSessionStore(root=".acpkit/langchain-sessions"),
    capability_bridges=[DeepAgentsCompatibilityBridge()],
    projection_maps=[DeepAgentsProjectionMap()],
    event_projection_maps=[StructuredEventProjectionMap()],
)
```

## Reading Order

- [Session State and Lifecycle](session-state.md)
- [Models, Modes, and Config](runtime-controls.md)
- [Plans, Thinking, and Approvals](plans-thinking-approvals.md)
- [Providers](providers.md)
- [Bridges](bridges.md)
- [Projections and Event Projection Maps](projections.md)
