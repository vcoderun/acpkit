# AdapterConfig

`AdapterConfig` is the main configuration object for `pydantic-acp`.

Use it to decide:

- what session state the adapter owns
- what state the host owns through providers
- which ACP-visible capabilities should be contributed by bridges
- how tools, output, and projections should be rendered

## Full Field Map

| Field | Type | Purpose |
|---|---|---|
| `agent_name` | `str` | ACP agent id |
| `agent_title` | `str` | Human-readable agent title |
| `agent_version` | `str` | Version reported to ACP clients |
| `allow_model_selection` | `bool` | Enables built-in model selection surface |
| `available_models` | `list[AdapterModel]` | Built-in model options |
| `models_provider` | `SessionModelsProvider \| None` | Host-owned model state |
| `modes_provider` | `SessionModesProvider \| None` | Host-owned mode state |
| `config_options_provider` | `ConfigOptionsProvider \| None` | Host-owned ACP config options |
| `plan_provider` | `PlanProvider \| None` | Host-owned plan state |
| `native_plan_persistence_provider` | `NativePlanPersistenceProvider \| None` | Callback for persisting native ACP plan state |
| `approval_bridge` | `ApprovalBridge \| None` | Live ACP approval workflow |
| `approval_state_provider` | `ApprovalStateProvider \| None` | Extra approval metadata exposed into session metadata |
| `capability_bridges` | `Sequence[CapabilityBridge]` | ACP-visible runtime extensions |
| `session_store` | `SessionStore` | Backing store for ACP sessions |
| `projection_maps` | `Sequence[ProjectionMap]` | Richer tool rendering |
| `hook_projection_map` | `HookProjectionMap \| None` | Hook event rendering controls |
| `tool_classifier` | `ToolClassifier` | Classifies tools for projection and metadata |
| `output_serializer` | `OutputSerializer` | Serializes final agent outputs into ACP transcript blocks |
| `enable_generic_tool_projection` | `bool` | Enables fallback tool projection |
| `enable_model_config_option` | `bool` | Controls whether the model picker is mirrored as an ACP config option |
| `replay_history_on_load` | `bool` | Replays transcript/message history when a session is loaded |

## A Practical Configuration

```python
from pathlib import Path

from pydantic_ai import Agent
from pydantic_acp import (
    AdapterConfig,
    AdapterModel,
    FileSessionStore,
    NativeApprovalBridge,
    ThinkingBridge,
    run_acp,
)

agent = Agent("openai:gpt-5", name="configured-agent")

config = AdapterConfig(
    agent_name="configured-agent",
    agent_title="Configured Agent",
    agent_version="2026.04",
    allow_model_selection=True,
    available_models=[
        AdapterModel(
            model_id="fast",
            name="Fast",
            description="Lower-latency responses.",
            override="openai:gpt-5-mini",
        ),
        AdapterModel(
            model_id="smart",
            name="Smart",
            description="Higher-quality responses.",
            override="openai:gpt-5",
        ),
    ],
    capability_bridges=[ThinkingBridge()],
    approval_bridge=NativeApprovalBridge(enable_persistent_choices=True),
    session_store=FileSessionStore(root=Path(".acp-sessions")),
)

run_acp(agent=agent, config=config)
```

## Choosing The Right State Owner

The most important `AdapterConfig` decision is ownership.

### Let The Adapter Own It

Prefer built-in config fields when:

- the state is local to this ACP server
- the UI should reflect the state directly
- there is no existing host authority that already owns it

Examples:

- `allow_model_selection` + `available_models`
- `session_store`
- `approval_bridge`
- `capability_bridges`

### Let The Host Own It

Prefer providers when:

- your app already persists the state elsewhere
- multiple ACP sessions should reflect product-level state
- the adapter should expose state, not invent it

Examples:

- `models_provider`
- `modes_provider`
- `config_options_provider`
- `plan_provider`
- `approval_state_provider`

## Model Selection Patterns

There are two common ways to expose models:

### Built-in model selection

Use this when the adapter can own the full model surface:

```python
AdapterConfig(
    allow_model_selection=True,
    available_models=[...],
)
```

### Provider-backed model selection

Use this when the host needs to own model ids, labels, policy, or persistence:

```python
AdapterConfig(
    models_provider=my_models_provider,
)
```

If a provider is present, the provider becomes authoritative.

## Plan Configuration Patterns

ACP plan support can come from two places:

### Provider-backed plans

Use `plan_provider` when your product already owns the plan state.

### Native plan state

Use `PrepareToolsBridge(plan_mode=True)` when you want the adapter to manage ACP plan state natively.

If you want each native plan update written to disk or synchronized elsewhere, add `native_plan_persistence_provider`.

`plan_provider` and native plan state are intentionally separate ownership models. Use one clear source of truth.

## Projection And Tool UX

Projection maps do not change what the model can do. They change how ACP clients see tool activity.

Common pattern:

```python
from pydantic_acp import FileSystemProjectionMap

AdapterConfig(
    projection_maps=[
        FileSystemProjectionMap(
            read_tool_names=frozenset({"read_repo_file"}),
            write_tool_names=frozenset({"write_workspace_file"}),
            bash_tool_names=frozenset({"run_command"}),
        )
    ]
)
```

This turns raw tool calls into richer ACP file diffs and command previews.

## Recommended Defaults

For most real integrations:

- use `FileSessionStore` in development and production
- prefer provider seams only when the host truly owns the state already
- keep `enable_generic_tool_projection=True`
- add `ThinkingBridge()` when your models support reasoning effort
- use `NativeApprovalBridge(enable_persistent_choices=True)` for approval-heavy agents

## Common Misconfigurations

- `FileSessionStore` takes `root=Path(...)`, not `base_dir=...`
- if `models_provider` is configured, it becomes authoritative over built-in `available_models`
- if `modes_provider` is configured, slash mode commands are derived from that provider’s mode ids
- native ACP plan tools only appear when your mode surface actually enables `plan_mode` or `plan_tools`
