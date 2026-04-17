from __future__ import annotations as _annotations

from concurrent.futures import ThreadPoolExecutor

from acp.interfaces import Agent as AcpAgent
from pydantic_acp import (
    AcpSessionContext,
    AdapterConfig,
    AgentBridgeBuilder,
    AgentFactory,
    BuiltinToolProjectionMap,
    CapabilityBridge,
    HistoryProcessorBridge,
    HookBridge,
    ImageGenerationBridge,
    IncludeToolReturnSchemasBridge,
    McpBridge,
    McpCapabilityBridge,
    McpServerDefinition,
    McpToolDefinition,
    MemorySessionStore,
    PrefixToolsBridge,
    PrepareToolsBridge,
    PrepareToolsMode,
    SetToolMetadataBridge,
    ThreadExecutorBridge,
    ToolsetBridge,
    WebFetchBridge,
    WebSearchBridge,
    create_acp_agent,
    run_acp,
)
from pydantic_ai import Agent
from pydantic_ai.capabilities import Toolset
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import RunContext, ToolDefinition
from pydantic_ai.toolsets.function import FunctionToolset

__all__ = ("build_adapter", "build_runtime", "main")


def build_runtime() -> tuple[AgentFactory[None, str], AdapterConfig]:
    hook_bridge = HookBridge()
    history_bridge = HistoryProcessorBridge()
    executor_bridge = ThreadExecutorBridge(executor=ThreadPoolExecutor(max_workers=4))

    def chat_tools(
        ctx: RunContext[None],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        del ctx, tool_defs
        return []

    def review_tools(
        ctx: RunContext[None],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        del ctx
        return list(tool_defs)

    prepare_bridge: PrepareToolsBridge[None] = PrepareToolsBridge(
        default_mode_id="chat",
        modes=[
            PrepareToolsMode(
                id="chat",
                name="Chat",
                description="Hide MCP tools while chatting.",
                prepare_func=chat_tools,
            ),
            PrepareToolsMode(
                id="review",
                name="Review",
                description="Expose MCP tools while reviewing.",
                prepare_func=review_tools,
            ),
        ],
    )
    mcp_bridge = McpBridge(
        servers=[
            McpServerDefinition(
                server_id="repo",
                name="Repo MCP",
                transport="http",
                tool_prefix="mcp.repo.",
            )
        ],
        tools=[
            McpToolDefinition(
                tool_name="mcp.repo.search",
                server_id="repo",
                kind="search",
            )
        ],
    )
    docs_toolset = FunctionToolset()

    @docs_toolset.tool_plain
    def docs_lookup(query: str) -> str:
        return f"docs:{query}"

    bridges: list[CapabilityBridge] = [
        hook_bridge,
        history_bridge,
        prepare_bridge,
        executor_bridge,
        ImageGenerationBridge(fallback_model="openai-responses:gpt-5.4"),
        WebSearchBridge(),
        WebFetchBridge(),
        ToolsetBridge(toolset=docs_toolset),
        PrefixToolsBridge(
            wrapped=Toolset(toolset=docs_toolset),
            prefix="docs",
        ),
        McpCapabilityBridge(url="https://example.com/mcp", allowed_tools=["search"]),
        SetToolMetadataBridge(tools=["mcp.repo.search"], code_mode=True),
        IncludeToolReturnSchemasBridge(tools=["mcp.repo.search"]),
        mcp_bridge,
    ]

    def trim_history(messages: list[ModelMessage]) -> list[ModelMessage]:
        return list(messages[-2:])

    def build_agent(session: AcpSessionContext) -> Agent[None, str]:
        builder = AgentBridgeBuilder(
            session=session,
            capability_bridges=bridges,
        )
        contributions = builder.build(plain_history_processors=[trim_history])
        agent = Agent(
            TestModel(
                call_tools=["mcp.repo.search"],
                custom_output_text="Bridge example complete.",
            ),
            name="bridge-example",
            capabilities=contributions.capabilities,
            history_processors=contributions.history_processors,
        )

        @agent.tool_plain(name="mcp.repo.search")
        def search_repo(query: str) -> str:
            return f"match:{query}"

        return agent

    config = AdapterConfig(
        capability_bridges=list(bridges),
        projection_maps=[BuiltinToolProjectionMap()],
        session_store=MemorySessionStore(),
    )
    return build_agent, config


def build_adapter() -> AcpAgent:
    agent_factory, config = build_runtime()
    return create_acp_agent(
        agent_factory=agent_factory,
        config=config,
    )


def main() -> None:
    agent_factory, config = build_runtime()
    run_acp(
        agent_factory=agent_factory,
        config=config,
    )


if __name__ == "__main__":
    main()
