# Plans, Thinking, And Approvals

These are the three ACP-specific workflows most teams care about once the adapter is already running:

- plan state
- reasoning effort
- approval-gated tools

## Native ACP Plan State

When `plan_provider` is not configured and the current mode supports native planning, the adapter injects hidden plan tools into the agent runtime.

### Plan access tools

These are available when the session supports native plan state:

- `acp_get_plan`
- `acp_set_plan`

### Plan progress tools

These are available when the current mode also supports plan progress:

- `acp_update_plan_entry`
- `acp_mark_plan_done`

`acp_get_plan` returns numbered entries, and those numbers are intentionally **1-based**.

## How Native Plan State Is Enabled

Mark one `PrepareToolsMode` as `plan_mode=True`:

```python
from pydantic_acp import PrepareToolsBridge, PrepareToolsMode

PrepareToolsBridge(
    default_mode_id="ask",
    modes=[
        PrepareToolsMode(
            id="plan",
            name="Plan",
            description="Draft ACP plan state.",
            prepare_func=plan_tools,
            plan_mode=True,
        ),
        PrepareToolsMode(
            id="agent",
            name="Agent",
            description="Execute the plan with the full tool surface.",
            prepare_func=agent_tools,
            plan_tools=True,
        ),
    ],
)
```

This pattern gives you:

- `plan` mode for drafting or revising the plan
- `agent` mode for working through plan entries without losing access to plan progress tools

## NativePlanGeneration

When native plan state is active in plan mode, the adapter also supports `NativePlanGeneration` structured output.

That lets a model return a structured plan directly instead of only emitting plain text.

The adapter then:

1. stores the plan on the session
2. emits an ACP plan update
3. keeps the plan state available for subsequent prompts

## Persisting Native Plans

If you want native plan updates mirrored to your own storage, use `native_plan_persistence_provider`.

That provider is called whenever the native ACP plan state changes.

A common use case is writing the current session plan into a workspace file while keeping ACP session state as the source of truth.

## Adding Plan-Specific Guidance

If your integration wants to keep ACP Kit's built-in native plan tool guidance but add product-specific planning instructions, use `AdapterConfig(native_plan_additional_instructions=...)`.

This appends extra guidance to the native plan summary returned by `acp_get_plan`. It does not replace the adapter's built-in guidance about 1-based plan entry numbers or the native ACP plan tool contract.

Use this for guidance such as:

- keep plans short
- avoid status churn for trivial same-turn tasks
- use `in_progress` only for multi-turn work

Use your own agent `instructions=` or session-aware factory when the guidance needs to be fully dynamic or not tied specifically to native plan state.

## Thinking Effort

`ThinkingBridge` makes Pydantic AI’s `Thinking` capability visible to ACP clients as session-local state.

The bridge contributes:

- a select config option
- session metadata describing the current and supported values
- per-run `ModelSettings` derived from `Thinking(...)`

That means ACP clients can offer a reasoning-effort UI without the adapter hard-coding provider-specific settings.

## Approval Flow

Approval support comes from `ApprovalBridge`, with `NativeApprovalBridge` as the default practical implementation.

Approval-gated tools typically look like this:

```python
from pydantic_ai import Agent
from pydantic_ai.exceptions import ApprovalRequired
from pydantic_ai.tools import RunContext

agent = Agent("openai:gpt-5", name="approval-agent")


@agent.tool
def delete_file(ctx: RunContext[None], path: str) -> str:
    if not ctx.tool_call_approved:
        raise ApprovalRequired()
    return f"Deleted: {path}"
```

With:

```python
from pydantic_acp import AdapterConfig, NativeApprovalBridge

config = AdapterConfig(
    approval_bridge=NativeApprovalBridge(enable_persistent_choices=True),
)
```

The bridge handles:

- deferred approval loops
- remembered approval policies
- ACP permission option rendering
- stable tool-call updates before and after approval

## Cancellation And Approval

Cancellation also matters here.

If a user stops a run while the adapter is waiting on approval:

- the approval request is marked as cancelled
- the session history stays valid
- the transcript receives a cancellation message instead of being left half-open

That behavior is important for editor-style “stop” controls.

## Choosing Between Native Plans And PlanProvider

Use native plan state when:

- the ACP session should own the active plan
- the model should manipulate the plan through ACP-native semantics
- you want plan mode and agent mode to collaborate on the same session plan

Use `PlanProvider` when:

- the host already owns the plan
- you are reflecting product-level planning state into ACP
- ACP should observe the plan, not be the source of truth
