# Providers

Providers let the host own session state while `pydantic-acp` remains the ACP adapter.

This is the right tool when state already belongs to the application or product layer and the adapter should reflect it, not reinvent it.

## Available Provider Interfaces

| Provider | Controls |
|---|---|
| `SessionModelsProvider` | available models, current model id, and model write-back |
| `SessionModesProvider` | available modes, current mode id, and mode write-back |
| `ConfigOptionsProvider` | extra ACP config options and config write-back |
| `PlanProvider` | ACP plan entries exposed for the session |
| `NativePlanPersistenceProvider` | persistence callback for adapter-owned native plan state |
| `ApprovalStateProvider` | extra approval metadata surfaced in session metadata |

## When Providers Are The Right Choice

Use a provider when:

- the host already stores the state
- a UI outside ACP is also reading or writing the state
- the state should survive adapter implementation changes
- you want the adapter to remain a thin translation layer

Do **not** reach for providers by default. If the adapter can own the state cleanly, built-in `AdapterConfig` fields are usually simpler.

## Example: Host-owned Models, Modes, Config, Plan, And Approval Metadata

```python
from dataclasses import dataclass, field

from acp.schema import (
    PlanEntry,
    SessionConfigOptionBoolean,
    SessionMode,
)
from pydantic_ai import Agent
from pydantic_acp import (
    AcpSessionContext,
    AdapterConfig,
    AdapterModel,
    ConfigOption,
    ModelSelectionState,
    ModeState,
)


@dataclass(slots=True)
class ExampleState:
    config_values: dict[str, dict[str, str | bool]] = field(default_factory=dict)

    def config_for(self, session: AcpSessionContext) -> dict[str, str | bool]:
        return self.config_values.setdefault(session.session_id, {})


@dataclass(slots=True, kw_only=True)
class ModelsProvider:
    state: ExampleState

    def get_model_state(
        self,
        session: AcpSessionContext,
        _agent: Agent[None, str],
    ) -> ModelSelectionState:
        config = self.state.config_for(session)
        return ModelSelectionState(
            available_models=[
                AdapterModel(
                    model_id="chat",
                    name="Chat",
                    description="Short conversational responses.",
                    override="openai:gpt-5-mini",
                ),
                AdapterModel(
                    model_id="review",
                    name="Review",
                    description="More deliberate review responses.",
                    override="openai:gpt-5",
                ),
            ],
            current_model_id=str(config.get("model_id", "chat")),
        )

    def set_model(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str],
        model_id: str,
    ) -> ModelSelectionState:
        config = self.state.config_for(session)
        config["model_id"] = model_id
        return self.get_model_state(session, agent)


@dataclass(slots=True, kw_only=True)
class ModesProvider:
    state: ExampleState

    def get_mode_state(
        self,
        session: AcpSessionContext,
        _agent: Agent[None, str],
    ) -> ModeState:
        config = self.state.config_for(session)
        return ModeState(
            modes=[
                SessionMode(id="chat", name="Chat", description="General conversation."),
                SessionMode(id="review", name="Review", description="Tool-heavy review mode."),
            ],
            current_mode_id=str(config.get("mode_id", "chat")),
        )

    def set_mode(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str],
        mode_id: str,
    ) -> ModeState:
        config = self.state.config_for(session)
        config["mode_id"] = mode_id
        return self.get_mode_state(session, agent)


@dataclass(slots=True, kw_only=True)
class ConfigProvider:
    state: ExampleState

    def get_config_options(
        self,
        session: AcpSessionContext,
        _agent: Agent[None, str],
    ) -> list[ConfigOption]:
        config = self.state.config_for(session)
        return [
            SessionConfigOptionBoolean(
                id="stream_enabled",
                name="Streaming",
                category="runtime",
                description="Enable streamed responses when the host supports them.",
                type="boolean",
                current_value=bool(config.get("stream_enabled", False)),
            )
        ]

    def set_config_option(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str],
        config_id: str,
        value: str | bool,
    ) -> list[ConfigOption] | None:
        if config_id != "stream_enabled" or not isinstance(value, bool):
            return None
        config = self.state.config_for(session)
        config["stream_enabled"] = value
        return self.get_config_options(session, agent)


@dataclass(slots=True, kw_only=True)
class PlanProvider:
    state: ExampleState

    def get_plan(
        self,
        session: AcpSessionContext,
        _agent: Agent[None, str],
    ) -> list[PlanEntry]:
        config = self.state.config_for(session)
        return [
            PlanEntry(
                content=f"mode:{config.get('mode_id', 'chat')}",
                priority="high",
                status="in_progress",
            )
        ]


@dataclass(slots=True, kw_only=True)
class ApprovalMetadataProvider:
    state: ExampleState

    def get_approval_state(
        self,
        session: AcpSessionContext,
        _agent: Agent[None, str],
    ) -> dict[str, str | bool]:
        config = self.state.config_for(session)
        return {
            "current_mode_id": str(config.get("mode_id", "chat")),
            "stream_enabled": bool(config.get("stream_enabled", False)),
        }


state = ExampleState()

config = AdapterConfig(
    models_provider=ModelsProvider(state=state),
    modes_provider=ModesProvider(state=state),
    config_options_provider=ConfigProvider(state=state),
    plan_provider=PlanProvider(state=state),
    approval_state_provider=ApprovalMetadataProvider(state=state),
)
```

This is the full provider pattern:

- `get_*` methods expose host-owned state into ACP
- `set_*` methods let ACP writes flow back into the host store
- the final `AdapterConfig(...)` wiring makes ownership explicit

## Provider Return Types

Two typed return objects do most of the work:

### `ModelSelectionState`

This carries:

- `available_models`
- `current_model_id`
- `allow_any_model_id`
- config-option display settings

### `ModeState`

This carries:

- `modes`
- `current_mode_id`

The adapter then transforms those values into ACP state updates and config options.

## Common Failure Modes

- implementing `get_model_state(...)` or `get_mode_state(...)` without the matching `set_*` method leaves ACP writes with nowhere to go
- returning mode ids like `model` or `thinking` will fail because those names are reserved for slash commands
- using `PlanProvider` and native ACP plan state as if they were the same source of truth usually creates conflicting behavior
- `ApprovalStateProvider` only contributes metadata; live approval flow still requires an `ApprovalBridge`

## Native Plan Persistence Provider

`NativePlanPersistenceProvider` is different from `PlanProvider`.

Use it when:

- the adapter owns the active ACP plan state
- but you still want a side effect whenever that plan changes

Typical use case:

- ACP session is the source of truth
- current plan is also written to `./.acpkit/plans/<session-id>.md`

## ApprovalStateProvider

This provider does not handle live approval requests. It only contributes metadata.

Examples of good approval metadata:

- remembered approval policy count
- whether the session is bound to a host context
- product-level approval scope or routing hints

Live approval flow still belongs to `ApprovalBridge`.
