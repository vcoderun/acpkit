from __future__ import annotations as _annotations

from acp.interfaces import Agent as AcpAgent
from pydantic_acp import (
    AcpSessionContext,
    AdapterConfig,
    AdapterModel,
    MemorySessionStore,
    create_acp_agent,
    run_acp,
)
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

__all__ = ("build_adapter", "build_agent", "build_config", "main")

FAST_MODEL = TestModel(custom_output_text="Fast workspace summary.")
REVIEW_MODEL = TestModel(custom_output_text="Detailed review summary.")


def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    default_model = REVIEW_MODEL if session.cwd.name == "review" else FAST_MODEL
    return Agent(
        default_model,
        name=f"factory-{session.cwd.name}",
        system_prompt=(
            "You are a session-aware ACP example. Mention the current workspace name when useful."
        ),
    )


def build_config() -> AdapterConfig:
    return AdapterConfig(
        allow_model_selection=True,
        available_models=[
            AdapterModel(
                model_id="fast",
                name="Fast",
                description="Low-latency summary mode.",
                override=FAST_MODEL,
            ),
            AdapterModel(
                model_id="review",
                name="Review",
                description="More deliberate review mode.",
                override=REVIEW_MODEL,
            ),
        ],
        session_store=MemorySessionStore(),
    )


def build_adapter() -> AcpAgent:
    return create_acp_agent(
        agent_factory=build_agent,
        config=build_config(),
    )


def main() -> None:
    run_acp(
        agent_factory=build_agent,
        config=build_config(),
    )


if __name__ == "__main__":
    main()
