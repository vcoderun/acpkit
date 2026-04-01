from __future__ import annotations as _annotations

from acp.interfaces import Agent as AcpAgent
from acp.interfaces import Client as AcpClient
from pydantic_acp import (
    AcpSessionContext,
    AdapterConfig,
    ClientHostContext,
    MemorySessionStore,
    create_acp_agent,
)
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import RunContext

__all__ = ("build_adapter", "build_agent", "build_host_context")


def build_host_context(
    *,
    client: AcpClient,
    session: AcpSessionContext,
) -> ClientHostContext:
    return ClientHostContext.from_session(
        client=client,
        session=session,
    )


def build_agent(
    *,
    client: AcpClient,
    session: AcpSessionContext,
) -> Agent[None, str]:
    host_context = build_host_context(client=client, session=session)
    agent = Agent(
        TestModel(call_tools=["read_workspace_note", "run_workspace_python"]),
        name="host-context-example",
    )

    @agent.tool
    async def read_workspace_note(ctx: RunContext[None]) -> str:
        del ctx
        response = await host_context.filesystem.read_text_file("notes/workspace.md")
        return response.content

    @agent.tool
    async def run_workspace_python(ctx: RunContext[None]) -> str:
        del ctx
        terminal = await host_context.terminal.create_terminal(
            "python",
            args=["-V"],
            cwd=str(session.cwd),
        )
        await host_context.terminal.wait_for_terminal_exit(terminal.terminal_id)
        output = await host_context.terminal.terminal_output(terminal.terminal_id)
        return output.output

    return agent


def build_adapter(client: AcpClient) -> AcpAgent:
    def agent_factory(session: AcpSessionContext) -> Agent[None, str]:
        return build_agent(client=client, session=session)

    adapter = create_acp_agent(
        agent_factory=agent_factory,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    adapter.on_connect(client)
    return adapter
