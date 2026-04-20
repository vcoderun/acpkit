# LangChain ACP Providers

Providers let the host own session state while `langchain-acp` remains the ACP
adapter.

This is the right shape when:

- model state already belongs to the application
- mode state already belongs to the application
- plan persistence already belongs to the application
- ACP should reflect that state instead of owning it

## Available Provider Interfaces

| Provider | Controls |
|---|---|
| `SessionModelsProvider` | available models, current model id, and model write-back |
| `SessionModesProvider` | available modes, current mode id, and mode write-back |
| `ConfigOptionsProvider` | extra ACP config options and config write-back |
| `PlanProvider` | ACP plan entries exposed for the session |
| `NativePlanPersistenceProvider` | persistence callback for adapter-owned native plan state |

## Built-in State vs Providers

Use built-in `AdapterConfig` fields when the adapter can own the control plane
cleanly:

- `available_models`
- `available_modes`
- `default_model_id`
- `default_mode_id`

Use providers when ACP is only one consumer of the state and the host app is
the real source of truth.

That is the preferred shape when:

- model ids come from a product catalog
- modes are policy-controlled
- config options should be mirrored from another service
- plan state should survive adapter restarts
- ACP is a view over host-owned state, not the owner of it

## Example

```python
from dataclasses import dataclass, field

from acp.schema import (
    ModelInfo,
    PlanEntry,
    SessionConfigSelectOption,
    SessionConfigOptionSelect,
    SessionMode,
)
from langchain_acp import (
    AcpSessionContext,
    ConfigOption,
    ModelSelectionState,
    ModeState,
)


@dataclass(slots=True)
class HostState:
    values: dict[str, dict[str, str]] = field(default_factory=dict)

    def config_for(self, session: AcpSessionContext) -> dict[str, str]:
        return self.values.setdefault(session.session_id, {})


@dataclass(slots=True)
class ModelsProvider:
    state: HostState

    def get_model_state(self, session: AcpSessionContext) -> ModelSelectionState:
        config = self.state.config_for(session)
        return ModelSelectionState(
            available_models=[
                ModelInfo(model_id="fast", name="Fast"),
                ModelInfo(model_id="deep", name="Deep"),
            ],
            current_model_id=config.get("model_id", "fast"),
        )

    def set_model(self, session: AcpSessionContext, model_id: str) -> ModelSelectionState:
        self.state.config_for(session)["model_id"] = model_id
        return self.get_model_state(session)


@dataclass(slots=True)
class ModesProvider:
    state: HostState

    def get_mode_state(self, session: AcpSessionContext) -> ModeState:
        config = self.state.config_for(session)
        return ModeState(
            modes=[
                SessionMode(id="ask", name="Ask"),
                SessionMode(id="agent", name="Agent"),
            ],
            current_mode_id=config.get("mode_id", "ask"),
        )

    def set_mode(self, session: AcpSessionContext, mode_id: str) -> ModeState:
        self.state.config_for(session)["mode_id"] = mode_id
        return self.get_mode_state(session)


@dataclass(slots=True)
class ConfigProvider:
    state: HostState

    def get_config_options(self, session: AcpSessionContext) -> list[ConfigOption]:
        config = self.state.config_for(session)
        return [
            SessionConfigOptionSelect(
                type="select",
                id="team",
                name="Team",
                category="runtime",
                description="Which team context to use for this session.",
                current_value=config.get("team", "general"),
                options=[
                    SessionConfigSelectOption(value="general", name="General"),
                    SessionConfigSelectOption(value="research", name="Research"),
                ],
            )
        ]

    def set_config_option(
        self,
        session: AcpSessionContext,
        option_id: str,
        value: str,
    ) -> list[ConfigOption]:
        self.state.config_for(session)[option_id] = value
        return self.get_config_options(session)


@dataclass(slots=True)
class PlanProvider:
    state: HostState

    def get_plan(self, session: AcpSessionContext) -> list[PlanEntry]:
        config = self.state.config_for(session)
        return [
            PlanEntry(
                content=f"Audit workspace for team {config.get('team', 'general')}",
                priority="high",
                status="in_progress",
            )
        ]
```

## Common Failure Modes

- treating `PlanProvider` and native adapter-owned plan state as one shared
  source of truth
- forgetting to implement the matching `set_*` method for an ACP-writable
  provider surface
- using providers when fixed `AdapterConfig` lists would be simpler
