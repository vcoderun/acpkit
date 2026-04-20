from __future__ import annotations as _annotations

import asyncio
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic_acp.bridges.capability_support import _json_user_location, _resolve_mcp_server_id
from pydantic_acp.session.state import utc_now
from pydantic_ai import ModelRequestContext, ModelResponse
from pydantic_ai.capabilities import MCP, ImageGeneration, Toolset
from pydantic_ai.messages import (
    CompactionPart,
    ModelMessage,
    ModelRequest,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.toolsets.function import FunctionToolset

from .support import (
    AcpSessionContext,
    AdapterConfig,
    Agent,
    AgentBridgeBuilder,
    AnthropicCompactionBridge,
    ImageGenerationBridge,
    IncludeToolReturnSchemasBridge,
    McpCapabilityBridge,
    MemorySessionStore,
    OpenAICompactionBridge,
    Path,
    PrefixToolsBridge,
    RecordingClient,
    SetToolMetadataBridge,
    TestModel,
    ThreadExecutorBridge,
    ToolCallProgress,
    ToolCallStart,
    ToolsetBridge,
    create_acp_agent,
    text_block,
)


def test_thread_executor_bridge_runs_sync_tools_on_configured_executor(
    tmp_path: Path,
) -> None:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="acpkit-bridge")
    bridge = ThreadExecutorBridge(executor=executor)

    try:

        def factory(session: AcpSessionContext) -> Agent[None, str]:
            builder = AgentBridgeBuilder(
                session=session,
                capability_bridges=[bridge],
            )
            contributions = builder.build()
            agent = Agent(
                TestModel(call_tools=["check_thread"], custom_output_text="done"),
                capabilities=contributions.capabilities,
            )

            @agent.tool_plain
            def check_thread() -> str:
                return threading.current_thread().name

            return agent

        adapter = create_acp_agent(
            agent_factory=factory,
            config=AdapterConfig(
                capability_bridges=[bridge],
                session_store=MemorySessionStore(),
            ),
        )
        client = RecordingClient()
        adapter.on_connect(client)

        session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
        response = asyncio.run(
            adapter.prompt(
                prompt=[text_block("Check the thread executor.")],
                session_id=session.session_id,
            )
        )

        assert response.stop_reason == "end_turn"
        progress_update = next(
            update
            for _, update in client.updates
            if isinstance(update, ToolCallProgress) and update.title == "check_thread"
        )
        assert isinstance(progress_update.raw_output, str)
        assert progress_update.raw_output.startswith("acpkit-bridge")
    finally:
        executor.shutdown(wait=True)


def test_metadata_and_return_schema_bridges_modify_selected_tools(
    tmp_path: Path,
) -> None:
    test_model = TestModel(custom_output_text="done")
    bridges = [
        SetToolMetadataBridge(tools=["tool_a"], code_mode=True),
        IncludeToolReturnSchemasBridge(tools=["tool_a"]),
    ]

    def factory(session: AcpSessionContext) -> Agent[None, str]:
        builder = AgentBridgeBuilder(
            session=session,
            capability_bridges=bridges,
        )
        contributions = builder.build()
        agent = Agent(
            test_model,
            capabilities=contributions.capabilities,
        )

        @agent.tool_plain
        def tool_a(x: int) -> int:
            return x

        @agent.tool_plain
        def tool_b(x: str) -> str:
            return x

        return agent

    adapter = create_acp_agent(
        agent_factory=factory,
        config=AdapterConfig(
            capability_bridges=bridges,
            session_store=MemorySessionStore(),
        ),
    )

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.prompt(
            prompt=[text_block("Inspect the available tools.")],
            session_id=session.session_id,
        )
    )

    assert response.stop_reason == "end_turn"
    params = test_model.last_model_request_parameters
    assert params is not None
    tool_a_def = next(tool_def for tool_def in params.function_tools if tool_def.name == "tool_a")
    tool_b_def = next(tool_def for tool_def in params.function_tools if tool_def.name == "tool_b")
    assert tool_a_def.metadata is not None
    assert tool_a_def.metadata["code_mode"] is True
    assert tool_a_def.include_return_schema is True
    assert "Return schema" in (tool_a_def.description or "")
    if tool_b_def.metadata is not None:
        assert "code_mode" not in tool_b_def.metadata
    assert tool_b_def.include_return_schema is not True
    assert "Return schema" not in (tool_b_def.description or "")


def test_image_generation_and_mcp_capability_bridges_build_metadata_and_classification() -> None:
    session = AcpSessionContext(
        session_id="session-1",
        cwd=Path("/tmp"),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    image_bridge = ImageGenerationBridge(
        fallback_model="openai-responses:gpt-5.4",
        quality="high",
        size="1024x1024",
        output_format="png",
    )
    mcp_bridge = McpCapabilityBridge(
        url="https://example.com/services/repo/sse",
        local=False,
        allowed_tools=["search", "read_file"],
        description="Repo MCP",
        authorization_token="secret",
    )

    image_capability = image_bridge.build_capability(session)
    mcp_capability = mcp_bridge.build_capability(session)

    assert isinstance(image_capability, ImageGeneration)
    assert isinstance(mcp_capability, MCP)
    assert image_bridge.get_tool_kind("image_generation") == "execute"
    assert image_bridge.get_tool_kind("generate_image") == "execute"
    assert image_bridge.get_session_metadata(session, Agent(TestModel())) == {
        "aspect_ratio": None,
        "background": None,
        "fallback_model": "openai-responses:gpt-5.4",
        "input_fidelity": None,
        "moderation": None,
        "output_compression": None,
        "output_format": "png",
        "quality": "high",
        "size": "1024x1024",
        "tool_names": ["generate_image", "image_generation"],
    }
    mcp_metadata = mcp_bridge.get_session_metadata(session, Agent(TestModel()))
    assert mcp_bridge.get_tool_kind("mcp_server:repo") == "execute"
    assert mcp_metadata["allowed_tools"] == ["read_file", "search"]
    assert mcp_metadata["description"] == "Repo MCP"
    assert mcp_metadata["has_authorization_token"] is True
    assert mcp_metadata["headers"] == []
    assert mcp_metadata["server_id"] == "example.com-sse"
    assert mcp_metadata["url"] == "https://example.com/services/repo/sse"


def test_toolset_and_prefix_bridges_expose_function_tools_to_the_model(
    tmp_path: Path,
) -> None:
    toolset = FunctionToolset()

    @toolset.tool_plain
    def lookup(query: str) -> str:
        return f"lookup:{query}"

    prefixed_toolset = FunctionToolset()

    @prefixed_toolset.tool_plain
    def search(term: str) -> str:
        return f"search:{term}"

    plain_model = TestModel(custom_output_text="done")
    prefixed_model = TestModel(custom_output_text="done")

    def plain_factory(session: AcpSessionContext) -> Agent[None, str]:
        builder = AgentBridgeBuilder(
            session=session,
            capability_bridges=[ToolsetBridge(toolset=toolset)],
        )
        contributions = builder.build()
        return Agent(
            plain_model,
            capabilities=contributions.capabilities,
        )

    def prefixed_factory(session: AcpSessionContext) -> Agent[None, str]:
        builder = AgentBridgeBuilder(
            session=session,
            capability_bridges=[
                PrefixToolsBridge(
                    wrapped=Toolset(toolset=prefixed_toolset),
                    prefix="repo",
                )
            ],
        )
        contributions = builder.build()
        return Agent(
            prefixed_model,
            capabilities=contributions.capabilities,
        )

    plain_adapter = create_acp_agent(
        agent_factory=plain_factory,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    plain_session = asyncio.run(plain_adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    plain_response = asyncio.run(
        plain_adapter.prompt(
            prompt=[text_block("Inspect tools.")],
            session_id=plain_session.session_id,
        )
    )
    assert plain_response.stop_reason == "end_turn"
    assert plain_model.last_model_request_parameters is not None
    assert [tool.name for tool in plain_model.last_model_request_parameters.function_tools] == [
        "lookup"
    ]

    prefixed_adapter = create_acp_agent(
        agent_factory=prefixed_factory,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    prefixed_session = asyncio.run(prefixed_adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    prefixed_response = asyncio.run(
        prefixed_adapter.prompt(
            prompt=[text_block("Inspect prefixed tools.")],
            session_id=prefixed_session.session_id,
        )
    )
    assert prefixed_response.stop_reason == "end_turn"
    assert prefixed_model.last_model_request_parameters is not None
    assert [tool.name for tool in prefixed_model.last_model_request_parameters.function_tools] == [
        "repo_search"
    ]


def test_toolset_bridge_preserves_instruction_parts_and_ordering(
    tmp_path: Path,
) -> None:
    user_toolset = FunctionToolset(instructions="User capability instructions.")
    bridge_toolset = FunctionToolset(instructions=lambda: "Bridge toolset instructions.")
    model = TestModel(custom_output_text="done")

    def factory(session: AcpSessionContext) -> Agent[None, str]:
        builder = AgentBridgeBuilder(
            session=session,
            capability_bridges=[ToolsetBridge(toolset=bridge_toolset)],
        )
        contributions = builder.build(capabilities=(Toolset(toolset=user_toolset),))
        return Agent(
            model,
            capabilities=contributions.capabilities,
        )

    adapter = create_acp_agent(
        agent_factory=factory,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.prompt(
            prompt=[text_block("Inspect toolset instructions.")],
            session_id=session.session_id,
        )
    )

    assert response.stop_reason == "end_turn"
    params = model.last_model_request_parameters
    assert params is not None
    assert params.instruction_parts is not None
    assert [(part.content, part.dynamic) for part in params.instruction_parts] == [
        ("User capability instructions.", False),
        ("Bridge toolset instructions.", True),
    ]


def test_mcp_toolset_include_instructions_reaches_model_request(tmp_path: Path) -> None:
    pytest.importorskip("mcp", exc_type=ImportError)
    from pydantic_ai.mcp import MCPServerStdio

    server_script = tmp_path / "mcp_stdio_server.py"
    server_script.write_text(
        "\n".join(
            (
                "from __future__ import annotations as _annotations",
                "",
                "from mcp.server.fastmcp import FastMCP",
                "",
                'mcp = FastMCP("test-mcp", instructions="Be a helpful assistant.")',
                "",
                "@mcp.tool()",
                "def ping() -> str:",
                '    return "pong"',
                "",
                'if __name__ == "__main__":',
                '    mcp.run("stdio")',
                "",
            )
        ),
        encoding="utf-8",
    )

    def return_instructions(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages
        return ModelResponse(parts=[TextPart(info.instructions or "")])

    agent = Agent(
        FunctionModel(return_instructions),
        toolsets=[
            MCPServerStdio(
                sys.executable,
                [str(server_script)],
                cwd=tmp_path,
                include_instructions=True,
                id="mcp",
            )
        ],
    )
    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.prompt(
            prompt=[text_block("Inspect MCP instructions.")],
            session_id=session.session_id,
        )
    )

    assert response.stop_reason == "end_turn"
    assert "Be a helpful assistant." in "".join(
        update.content.text
        for _, update in client.updates
        if getattr(update, "sessionUpdate", None) == "agent_message_chunk"
    )


def test_capability_bridge_helper_and_metadata_edge_paths() -> None:
    session = AcpSessionContext(
        session_id="session-2",
        cwd=Path("/tmp"),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    agent = Agent(TestModel())
    image_bridge = ImageGenerationBridge()
    mcp_bridge = McpCapabilityBridge(
        url="https://example.com/",
        id="repo-server",
        headers={"authorization": "Bearer x"},
    )
    toolset_bridge = ToolsetBridge(toolset=cast(Any, SimpleNamespace(id=object())))
    prefix_bridge = PrefixToolsBridge(wrapped=Toolset(toolset=FunctionToolset()), prefix="repo")
    openai_bridge = OpenAICompactionBridge(message_count_threshold=4, instructions="compact")
    anthropic_bridge = AnthropicCompactionBridge(
        token_threshold=10,
        instructions="compact",
        pause_after_compaction=True,
    )

    assert _resolve_mcp_server_id("https://example.com/tools/sse", "explicit-id") == "explicit-id"
    assert _resolve_mcp_server_id("https://example.com/", None) == "example.com"
    assert _resolve_mcp_server_id("urn:acpkit", None) == "acpkit"
    assert _json_user_location(cast(Any, {"city": "Istanbul", "ignored": object()})) == {
        "city": "Istanbul"
    }

    assert len(image_bridge.build_agent_capabilities(session)) == 1
    assert len(toolset_bridge.build_agent_capabilities(session)) == 1
    assert len(prefix_bridge.build_agent_capabilities(session)) == 1
    assert len(openai_bridge.build_agent_capabilities(session)) == 1
    assert openai_bridge.get_session_metadata(session, agent) == {
        "has_trigger": False,
        "instructions": "compact",
        "message_count_threshold": 4,
    }
    assert toolset_bridge.get_session_metadata(session, agent) == {
        "toolset_id": None,
        "toolset_type": "SimpleNamespace",
    }
    assert prefix_bridge.get_session_metadata(session, agent) == {
        "prefix": "repo",
        "wrapped_capability": "Toolset",
    }
    assert prefix_bridge.get_tool_kind("search") is None
    assert mcp_bridge.get_session_metadata(session, agent)["server_id"] == "repo-server"
    assert mcp_bridge.get_tool_kind("repo.search") is None
    try:
        capabilities = mcp_bridge.build_agent_capabilities(session)
    except ImportError as exc:
        assert "mcp" in str(exc).lower()
    else:
        assert len(capabilities) == 1

    try:
        capabilities = anthropic_bridge.build_agent_capabilities(session)
    except ImportError as exc:
        assert "anthropic" in str(exc).lower()
    else:
        assert len(capabilities) == 1
    assert anthropic_bridge.get_session_metadata(session, agent) == {
        "instructions": "compact",
        "pause_after_compaction": True,
        "token_threshold": 10,
    }


def test_provider_specific_compaction_bridges_build_capabilities_and_metadata() -> None:
    session = AcpSessionContext(
        session_id="session-2",
        cwd=Path("/tmp"),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    openai_bridge = OpenAICompactionBridge(
        message_count_threshold=10,
        instructions="Compact aggressively.",
    )
    anthropic_bridge = AnthropicCompactionBridge(
        token_threshold=90_000,
        instructions="Compact safely.",
        pause_after_compaction=True,
    )

    openai_capability = openai_bridge.build_capability(session)
    assert openai_capability.get_serialization_name() == "OpenAICompaction"
    try:
        anthropic_capability = anthropic_bridge.build_capability(session)
    except ImportError as exc:
        assert "anthropic" in str(exc).lower()
    else:
        assert type(anthropic_capability).__name__ == "AnthropicCompaction"
    assert openai_bridge.get_session_metadata(session, Agent(TestModel())) == {
        "has_trigger": False,
        "instructions": "Compact aggressively.",
        "message_count_threshold": 10,
    }
    assert anthropic_bridge.get_session_metadata(session, Agent(TestModel())) == {
        "instructions": "Compact safely.",
        "pause_after_compaction": True,
        "token_threshold": 90000,
    }


def test_anthropic_compaction_bridge_can_build_with_stubbed_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AcpSessionContext(
        session_id="session-anthropic",
        cwd=Path("/tmp"),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    bridge = AnthropicCompactionBridge(
        token_threshold=12,
        instructions="Compact.",
        pause_after_compaction=True,
    )

    class StubAnthropicCompaction:
        def __init__(
            self,
            *,
            token_threshold: int,
            instructions: str | None,
            pause_after_compaction: bool,
        ) -> None:
            self.token_threshold = token_threshold
            self.instructions = instructions
            self.pause_after_compaction = pause_after_compaction

    monkeypatch.setitem(
        sys.modules,
        "pydantic_ai.models.anthropic",
        SimpleNamespace(AnthropicCompaction=StubAnthropicCompaction),
    )

    capability = bridge.build_capability(session)
    assert isinstance(capability, StubAnthropicCompaction)
    assert capability.token_threshold == 12


def test_openai_compaction_bridge_records_visible_start_and_completion() -> None:
    session = AcpSessionContext(
        session_id="session-3",
        cwd=Path("/tmp"),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    bridge = OpenAICompactionBridge(
        message_count_threshold=1,
        instructions="Compact aggressively.",
    )
    capability = bridge.build_capability(session)
    fake_model = cast(Any, object.__new__(_FakeOpenAIResponsesModel))
    request_context = ModelRequestContext(
        model=fake_model,
        messages=[
            ModelRequest(parts=[UserPromptPart(content="old")]),
            ModelRequest(parts=[UserPromptPart(content="new")]),
        ],
        model_settings=None,
        model_request_parameters=cast(Any, SimpleNamespace()),
    )

    updated_context = asyncio.run(
        capability.before_model_request(
            cast(Any, SimpleNamespace()),
            request_context,
        )
    )

    assert len(updated_context.messages) == 2
    updates = bridge.drain_updates(session, Agent(TestModel()))
    assert updates is not None
    assert len(updates) == 2
    start_update = updates[0]
    progress_update = updates[1]
    assert isinstance(start_update, ToolCallStart)
    assert isinstance(progress_update, ToolCallProgress)
    assert start_update.title == "Context Compaction"
    assert start_update.status == "in_progress"
    assert start_update.raw_input == {
        "provider": "openai",
        "instructions": "Compact aggressively.",
        "message_count": 2,
    }
    assert progress_update.tool_call_id == start_update.tool_call_id
    assert progress_update.status == "completed"
    assert progress_update.raw_output == "\n".join(
        (
            "Provider: openai",
            "Status: history compacted",
            "Compaction payload stored for round-trip.",
            "Compaction id: cmp-123",
        )
    )


class _FakeOpenAIResponsesModel(OpenAIResponsesModel):
    async def compact_messages(
        self,
        request_context: ModelRequestContext,
        *,
        instructions: str | None = None,
    ) -> ModelResponse:
        del request_context, instructions
        from pydantic_ai import CompactionPart

        return ModelResponse(
            parts=[CompactionPart(id="cmp-123", provider_name="openai", provider_details={})]
        )


class _FailingOpenAIResponsesModel(OpenAIResponsesModel):
    async def compact_messages(
        self,
        request_context: ModelRequestContext,
        *,
        instructions: str | None = None,
    ) -> ModelResponse:
        del request_context, instructions
        raise RuntimeError("boom")


def test_openai_compaction_helpers_cover_trigger_threshold_and_missing_parts() -> None:
    from pydantic_acp.bridges.capability_support import (
        _extract_compaction_part,
        _format_openai_compaction_output,
        _should_openai_compact,
    )

    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="one")]),
        ModelRequest(parts=[UserPromptPart(content="two")]),
    ]

    assert (
        _should_openai_compact(
            messages,
            trigger=lambda payload: len(payload) == 2,
            message_count_threshold=None,
        )
        is True
    )
    assert (
        _should_openai_compact(
            messages,
            trigger=lambda payload: len(payload) > 10,
            message_count_threshold=0,
        )
        is False
    )
    assert (
        _should_openai_compact(
            messages,
            trigger=None,
            message_count_threshold=1,
        )
        is True
    )
    assert (
        _should_openai_compact(
            messages,
            trigger=None,
            message_count_threshold=5,
        )
        is False
    )
    assert (
        _should_openai_compact(
            messages,
            trigger=None,
            message_count_threshold=None,
        )
        is False
    )

    request_context = ModelRequestContext(
        model=cast(Any, object.__new__(_FakeOpenAIResponsesModel)),
        messages=[
            ModelRequest(parts=[UserPromptPart(content="prompt")]),
            ModelResponse(parts=[]),
            ModelResponse(
                parts=[CompactionPart(id=None, provider_name="openai", provider_details={})]
            ),
        ],
        model_settings=None,
        model_request_parameters=cast(Any, SimpleNamespace()),
    )

    compacted_part = _extract_compaction_part(request_context.messages)
    assert compacted_part is not None
    assert compacted_part.id is None
    assert _format_openai_compaction_output(request_context) == "\n".join(
        (
            "Provider: openai",
            "Status: history compacted",
            "Compaction payload stored for round-trip.",
        )
    )

    assert (
        _extract_compaction_part([ModelRequest(parts=[UserPromptPart(content="prompt")])]) is None
    )
    assert (
        _extract_compaction_part(
            [
                ModelResponse(
                    parts=[
                        TextPart(content="skip"),
                        CompactionPart(
                            id="cmp-456",
                            provider_name="openai",
                            provider_details={},
                        ),
                    ]
                )
            ]
        )
        is not None
    )


def test_openai_compaction_bridge_skips_when_not_needed_and_records_failures() -> None:
    session = AcpSessionContext(
        session_id="session-4",
        cwd=Path("/tmp"),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    skipped_bridge = OpenAICompactionBridge(message_count_threshold=5)
    skipped_capability = skipped_bridge.build_capability(session)
    skipped_context = ModelRequestContext(
        model=cast(Any, object.__new__(_FakeOpenAIResponsesModel)),
        messages=[
            ModelRequest(parts=[UserPromptPart(content="old")]),
            ModelRequest(parts=[UserPromptPart(content="new")]),
        ],
        model_settings=None,
        model_request_parameters=cast(Any, SimpleNamespace()),
    )

    unchanged_context = asyncio.run(
        skipped_capability.before_model_request(
            cast(Any, SimpleNamespace()),
            skipped_context,
        )
    )

    assert unchanged_context is skipped_context
    assert skipped_bridge.drain_updates(session, Agent(TestModel())) is None

    failing_bridge = OpenAICompactionBridge(
        trigger=lambda _messages: True,
        instructions="Compact now.",
    )
    failing_capability = failing_bridge.build_capability(session)
    failing_context = ModelRequestContext(
        model=cast(Any, object.__new__(_FailingOpenAIResponsesModel)),
        messages=[
            ModelRequest(parts=[UserPromptPart(content="old")]),
            ModelRequest(parts=[UserPromptPart(content="new")]),
        ],
        model_settings=None,
        model_request_parameters=cast(Any, SimpleNamespace()),
    )

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(
            failing_capability.before_model_request(
                cast(Any, SimpleNamespace()),
                failing_context,
            )
        )

    updates = failing_bridge.drain_updates(session, Agent(TestModel()))
    assert updates is not None
    assert len(updates) == 2
    start_update = updates[0]
    progress_update = updates[1]
    assert isinstance(start_update, ToolCallStart)
    assert isinstance(progress_update, ToolCallProgress)
    assert start_update.title == "Context Compaction"
    assert progress_update.tool_call_id == start_update.tool_call_id
    assert progress_update.status == "failed"
    assert progress_update.raw_output == "Provider: openai\nStatus: failed\nError: boom"
