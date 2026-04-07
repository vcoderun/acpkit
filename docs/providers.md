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
