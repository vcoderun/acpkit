# LangChain ACP Plans, Thinking, And Approvals

This page groups three related concerns:

- ACP-native plan state
- approval flow
- reasoning-effort differences from `pydantic-acp`

## Native Plan State

`langchain-acp` supports ACP-native plans through:

- `TaskPlan`
- `PlanGenerationType`
- `acp_get_plan`
- `acp_set_plan`
- `acp_update_plan_entry`
- `acp_mark_plan_done`
- `native_plan_tools(...)`

Important config fields:

- `plan_mode_id`
- `default_plan_generation_type`
- `enable_plan_progress_tools`
- `plan_provider`
- `native_plan_persistence_provider`

This gives the adapter a truthful ACP-native plan surface even when the upstream
graph runtime does not have one.

The adapter does not need DeepAgents to own plan truth. It can publish its own
ACP plan state and still extract compatible `write_todos` payloads when the
graph happens to emit them.

## Tool-based vs Structured Plans

`PlanGenerationType` supports two modes:

- `structured`
- `tools`

Use `structured` when the graph can produce a structured plan object directly.
Use `tools` when plan state should be written through ACP-native plan tools over
the course of the run.

`tools` is the safer fallback when plan generation must stay visible to ACP
clients turn by turn.

## DeepAgents Compatibility

DeepAgents compatibility is layered on top, not treated as the core truth
source.

The main compatibility seam is:

- `DeepAgentsCompatibilityBridge`

It can extract plan entries from `write_todos`-style payloads while leaving ACP
native plan ownership intact.

## Approvals

LangChain-side approval flow maps naturally onto ACP when the runtime uses
`HumanInTheLoopMiddleware`.

Adapter surfaces:

- `ApprovalBridge`
- `NativeApprovalBridge`
- ACP permission requests
- resume decisions

When the graph pauses for approval, the ACP session pauses too. The adapter does
not flatten that into plain text.

If the runtime does not use `HumanInTheLoopMiddleware`, approval bridging
usually belongs in the host product layer instead of the generic adapter core.

## What About Thinking?

Unlike `pydantic-acp`, `langchain-acp` does not currently expose a standalone
`ThinkingBridge`-style surface.

That is intentional:

- LangChain and LangGraph do not provide one shared first-class reasoning-effort
  abstraction across compiled graphs
- ACP Kit should not invent one generic control if the upstream runtime does not
  own it consistently

If a host product has a real reasoning-effort control, expose it through
`ConfigOptionsProvider` or a custom bridge instead of pretending there is one
universal LangChain thinking API.

## Example

```python
from langchain_acp import AdapterConfig, NativeApprovalBridge

config = AdapterConfig(
    plan_mode_id="plan",
    default_plan_generation_type="structured",
    enable_plan_progress_tools=True,
    approval_bridge=NativeApprovalBridge(),
)
```

## Reading Order

- [Models, Modes, and Config](runtime-controls.md)
- [Providers](providers.md)
- [Bridges](bridges.md)
- [Projections and Event Projection Maps](projections.md)
