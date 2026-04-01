from __future__ import annotations as _annotations

from dataclasses import dataclass, field
from typing import TypeAlias

from acp.interfaces import Agent as AcpAgent
from acp.schema import (
    PlanEntry,
    SessionConfigOptionBoolean,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SessionMode,
)
from pydantic_acp import (
    AcpSessionContext,
    AdapterConfig,
    AdapterModel,
    ConfigOption,
    MemorySessionStore,
    ModelSelectionState,
    ModeState,
    create_acp_agent,
    run_acp,
)
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

__all__ = ("build_adapter", "build_config", "build_state", "main")

CHAT_MODEL = TestModel(custom_output_text="Provider-backed chat response.")
REVIEW_MODEL = TestModel(custom_output_text="Provider-backed review response.")
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(slots=True)
class ExampleProviderState:
    config_values: dict[str, dict[str, str | bool]] = field(default_factory=dict)

    def config_for(self, session: AcpSessionContext) -> dict[str, str | bool]:
        return self.config_values.setdefault(session.session_id, {})


@dataclass(slots=True, kw_only=True)
class ExampleModelsProvider:
    state: ExampleProviderState

    def get_model_state(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str],
    ) -> ModelSelectionState:
        del agent
        config = self.state.config_for(session)
        current_model_id = str(config.get("model_id", "chat"))
        return ModelSelectionState(
            available_models=[
                AdapterModel(
                    model_id="chat",
                    name="Chat",
                    description="Short conversational answers.",
                    override=CHAT_MODEL,
                ),
                AdapterModel(
                    model_id="review",
                    name="Review",
                    description="Longer review-style answers.",
                    override=REVIEW_MODEL,
                ),
            ],
            current_model_id=current_model_id,
        )

    def set_model(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str],
        model_id: str,
    ) -> ModelSelectionState:
        self.state.config_for(session)["model_id"] = model_id
        return self.get_model_state(session, agent)


@dataclass(slots=True, kw_only=True)
class ExampleModesProvider:
    state: ExampleProviderState

    def get_mode_state(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str],
    ) -> ModeState:
        del agent
        config = self.state.config_for(session)
        current_mode_id = str(config.get("mode_id", "chat"))
        return ModeState(
            modes=[
                SessionMode(
                    id="chat",
                    name="Chat",
                    description="General conversation mode.",
                ),
                SessionMode(
                    id="review",
                    name="Review",
                    description="Tool-heavy review mode.",
                ),
            ],
            current_mode_id=current_mode_id,
        )

    def set_mode(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str],
        mode_id: str,
    ) -> ModeState:
        self.state.config_for(session)["mode_id"] = mode_id
        return self.get_mode_state(session, agent)


@dataclass(slots=True, kw_only=True)
class ExampleConfigOptionsProvider:
    state: ExampleProviderState

    def get_config_options(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str],
    ) -> list[ConfigOption]:
        del agent
        config = self.state.config_for(session)
        stream_enabled = bool(config.get("stream_enabled", False))
        approval_scope = str(config.get("approval_scope", "tool"))
        return [
            SessionConfigOptionBoolean(
                id="stream_enabled",
                name="Streaming",
                category="runtime",
                description="Enable streamed responses when the host supports them.",
                type="boolean",
                current_value=stream_enabled,
            ),
            SessionConfigOptionSelect(
                id="approval_scope",
                name="Approval Scope",
                category="runtime",
                description="Choose how approval decisions are grouped.",
                type="select",
                current_value=approval_scope,
                options=[
                    SessionConfigSelectOption(value="tool", name="Per Tool"),
                    SessionConfigSelectOption(value="session", name="Per Session"),
                ],
            ),
        ]

    def set_config_option(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str],
        config_id: str,
        value: str | bool,
    ) -> list[ConfigOption] | None:
        config = self.state.config_for(session)
        if config_id == "stream_enabled" and isinstance(value, bool):
            config["stream_enabled"] = value
            return self.get_config_options(session, agent)
        if config_id == "approval_scope" and isinstance(value, str):
            config["approval_scope"] = value
            return self.get_config_options(session, agent)
        return None


@dataclass(slots=True, kw_only=True)
class ExamplePlanProvider:
    state: ExampleProviderState

    def get_plan(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str],
    ) -> list[PlanEntry]:
        del agent
        config = self.state.config_for(session)
        current_mode = str(config.get("mode_id", "chat"))
        stream_enabled = bool(config.get("stream_enabled", False))
        return [
            PlanEntry(content=f"mode:{current_mode}", priority="high", status="in_progress"),
            PlanEntry(
                content=f"stream:{str(stream_enabled).lower()}",
                priority="low",
                status="pending",
            ),
        ]


@dataclass(slots=True, kw_only=True)
class ExampleApprovalStateProvider:
    state: ExampleProviderState

    def get_approval_state(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str],
    ) -> dict[str, JsonValue]:
        del agent
        config = self.state.config_for(session)
        return {
            "policy": str(config.get("approval_scope", "tool")),
            "stream_enabled": bool(config.get("stream_enabled", False)),
        }


def build_state() -> ExampleProviderState:
    return ExampleProviderState()


def build_config() -> AdapterConfig:
    state = build_state()
    return AdapterConfig(
        approval_state_provider=ExampleApprovalStateProvider(state=state),
        config_options_provider=ExampleConfigOptionsProvider(state=state),
        models_provider=ExampleModelsProvider(state=state),
        modes_provider=ExampleModesProvider(state=state),
        plan_provider=ExamplePlanProvider(state=state),
        session_store=MemorySessionStore(),
    )


def build_adapter() -> AcpAgent:
    return create_acp_agent(
        agent=Agent(
            CHAT_MODEL,
            name="provider-example",
            system_prompt="You are an ACP example backed by explicit session providers.",
        ),
        config=build_config(),
    )


def main() -> None:
    run_acp(
        agent=Agent(
            CHAT_MODEL,
            name="provider-example",
            system_prompt="You are an ACP example backed by explicit session providers.",
        ),
        config=build_config(),
    )


if __name__ == "__main__":
    main()
