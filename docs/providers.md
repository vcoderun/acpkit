# Providers

Providers let the host own richer session state while the adapter stays truthful about what it can
expose through ACP.

Use providers when the session state already belongs to the host or product layer, rather than the
agent itself.

## Available Provider Interfaces

### SessionModelsProvider

Controls:

- available ACP models
- current model id
- optional free-form model ids
- model write-back for `set_session_model(...)`

Key return type:

- `ModelSelectionState`

### SessionModesProvider

Controls:

- available ACP modes
- current mode id
- mode write-back for `set_session_mode(...)`

Key return type:

- `ModeState`

### ConfigOptionsProvider

Controls:

- additional ACP config options
- config write-back for `set_config_option(...)`

Supported option types:

- `SessionConfigOptionSelect`
- `SessionConfigOptionBoolean`

### PlanProvider

Controls:

- ACP `PlanEntry` emission for current session state
- plan updates during session bootstrap and prompt execution

### ApprovalStateProvider

Controls:

- approval metadata surfaced into session metadata

This is distinct from the live approval flow handled by `ApprovalBridge`.

## Native Plan State

When `plan_provider` is not configured on `AdapterConfig`, the adapter can manage plan state
natively without any provider, as long as a `PrepareToolsBridge` mode has `plan_mode=True` and
that mode is currently active.

### How It Works

When native plan state is active, the adapter automatically installs two hidden tools on the
agent:

- `acp_get_plan`
  returns the current plan as a formatted string. The text is the stored `plan_markdown` if
  available, otherwise a bullet list derived from the stored `plan_entries`.
- `acp_set_plan`
  accepts a list of `PlanEntry` objects and an optional `plan_md` string, then writes both into
  session state and emits an ACP plan update.

These tools are only exposed when native plan state is active. They are kept out of the normal
tool listing and do not appear in `/tools` output.

### NativePlanGeneration Output Type

The adapter also extends the agent's `output_type` with `NativePlanGeneration` while native plan
state is active. When the agent returns a `NativePlanGeneration` value, the adapter:

1. stores the embedded `plan_entries` and `plan_md` into the session
2. emits an ACP plan update
3. suppresses that structured output from the normal ACP message stream

`NativePlanGeneration` fields:

| Field | Type | Description |
|---|---|---|
| `plan_entries` | `list[PlanEntry]` | Structured plan entries to store |
| `plan_md` | `str` | Optional markdown text for the plan |

This lets an agent produce a full plan in a single structured response instead of calling
`acp_set_plan` as a tool call.

### Activating Native Plan State

Native plan state is activated by marking one `PrepareToolsMode` with `plan_mode=True` inside a
`PrepareToolsBridge` and making that bridge available in `AdapterConfig.capability_bridges`.

```python
from pydantic_ai import Agent
from pydantic_ai.tools import RunContext, ToolDefinition
from pydantic_acp import AdapterConfig, PrepareToolsBridge, PrepareToolsMode, run_acp


def plan_tools(
    ctx: RunContext[None], tool_defs: list[ToolDefinition]
) -> list[ToolDefinition]:
    del ctx
    return list(tool_defs)


def agent_tools(
    ctx: RunContext[None], tool_defs: list[ToolDefinition]
) -> list[ToolDefinition]:
    del ctx
    return list(tool_defs)


agent = Agent("openai:gpt-5", name="plan-agent")

run_acp(
    agent=agent,
    config=AdapterConfig(
        capability_bridges=[
            PrepareToolsBridge(
                default_mode_id="agent",
                modes=[
                    PrepareToolsMode(
                        id="plan",
                        name="Plan",
                        description="Inspect and write plans.",
                        prepare_func=plan_tools,
                        plan_mode=True,
                    ),
                    PrepareToolsMode(
                        id="agent",
                        name="Agent",
                        description="Full tool surface.",
                        prepare_func=agent_tools,
                    ),
                ],
            ),
        ],
    ),
)
```

When the session switches to the `plan` mode, `acp_get_plan` and `acp_set_plan` become available
to the agent, and the output type is extended with `NativePlanGeneration`.

### Interaction with PlanProvider

Native plan state and `PlanProvider` are mutually exclusive. If `AdapterConfig.plan_provider` is
set, the adapter delegates all plan emission to the provider and the native plan tools are never
installed.

## Example

```python
from dataclasses import dataclass

from acp.schema import PlanEntry, SessionMode, SessionConfigOptionBoolean
from pydantic_ai import Agent
from pydantic_acp import (
    AdapterConfig,
    AdapterModel,
    ConfigOption,
    ModelSelectionState,
    ModeState,
    run_acp,
)

@dataclass
class ModelsProvider:
    def get_model_state(self, session, agent: Agent) -> ModelSelectionState:
        del agent
        return ModelSelectionState(
            available_models=[
                AdapterModel(model_id="fast", name="Fast", override="openai:gpt-5-mini"),
                AdapterModel(model_id="smart", name="Smart", override="openai:gpt-5"),
            ],
            current_model_id=str(session.config_values.get("model", "smart")),
        )

    def set_model(self, session, agent: Agent, model_id: str) -> ModelSelectionState:
        del agent
        session.config_values["model"] = model_id
        return self.get_model_state(session, agent)

@dataclass
class ModesProvider:
    def get_mode_state(self, session, agent: Agent) -> ModeState:
        del agent
        return ModeState(
            modes=[
                SessionMode(id="chat", name="Chat", description="Conversational mode"),
                SessionMode(id="code", name="Code", description="Code generation mode"),
            ],
            current_mode_id=str(session.config_values.get("mode", "chat")),
        )

    def set_mode(self, session, agent: Agent, mode_id: str) -> ModeState:
        del agent
        session.config_values["mode"] = mode_id
        return self.get_mode_state(session, agent)

@dataclass
class ConfigProvider:
    def get_config_options(self, session, agent: Agent) -> list[ConfigOption]:
        del agent
        return [
            SessionConfigOptionBoolean(
                id="verbose",
                name="Verbose Mode",
                category="output",
                description="Show detailed responses",
                type="boolean",
                current_value=bool(session.config_values.get("verbose", False)),
            )
        ]

    def set_config_option(self, session, agent: Agent, config_id: str, value: str | bool):
        del agent
        if config_id == "verbose" and isinstance(value, bool):
            session.config_values["verbose"] = value
            return self.get_config_options(session, agent)
        return None

@dataclass
class PlanProvider:
    def get_plan(self, session, agent: Agent) -> list[PlanEntry]:
        del agent
        return [
            PlanEntry(
                content=f"Current mode: {session.config_values.get('mode', 'chat')}",
                priority="high",
                status="in_progress",
            )
        ]

agent = Agent("openai:gpt-5", name="provider-agent")

run_acp(
    agent=agent,
    config=AdapterConfig(
        models_provider=ModelsProvider(),
        modes_provider=ModesProvider(),
        config_options_provider=ConfigProvider(),
        plan_provider=PlanProvider(),
    ),
)
```

## When To Use Providers

Use providers when the session state already belongs to the host or product layer:

- available models come from host policy
- mode selection is product-defined
- config options are product-specific
- plan state is generated externally
- approval metadata is stored outside the adapter core

## Return Shape

Provider methods may return:

- a concrete value
- `None`
- an awaitable of either

That means sync and async host integrations are both supported.
