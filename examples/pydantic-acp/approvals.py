from __future__ import annotations as _annotations

from acp.interfaces import Agent as AcpAgent
from pydantic_acp import (
    AdapterConfig,
    MemorySessionStore,
    NativeApprovalBridge,
    create_acp_agent,
    run_acp,
)
from pydantic_ai import Agent
from pydantic_ai.exceptions import ApprovalRequired
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import RunContext

__all__ = ("build_adapter", "build_agent", "build_config", "main")


def build_agent() -> Agent[None, str]:
    agent = Agent(
        TestModel(call_tools=["write_release_note"]),
        name="approval-example",
        system_prompt="Use the release-note tool when the user asks for deployment changes.",
    )

    @agent.tool
    def write_release_note(ctx: RunContext[None], target: str) -> str:
        if not ctx.tool_call_approved:
            raise ApprovalRequired()
        return f"approved:{target}"

    return agent


def build_config() -> AdapterConfig:
    return AdapterConfig(
        approval_bridge=NativeApprovalBridge(enable_persistent_choices=True),
        session_store=MemorySessionStore(),
    )


def build_adapter() -> AcpAgent:
    return create_acp_agent(
        agent=build_agent(),
        config=build_config(),
    )


def main() -> None:
    run_acp(
        agent=build_agent(),
        config=build_config(),
    )


if __name__ == "__main__":
    main()
