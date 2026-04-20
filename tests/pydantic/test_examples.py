from __future__ import annotations as _annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic_acp.types import (
    AudioContentBlock,
    BlobResourceContents,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    PlanEntry,
    ResourceContentBlock,
    TextResourceContents,
)
from pydantic_ai import ModelRequest, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from examples.pydantic import finance_agent, travel_agent

from .support import (
    UTC,
    AcpSessionContext,
    RecordingClient,
    agent_message_texts,
    create_acp_agent,
    datetime,
    text_block,
)


def _resolve_result(value: Any) -> Any:
    return asyncio.run(value) if asyncio.iscoroutine(value) else value


def _finance_text_model(
    messages: list[ModelRequest | ModelResponse],
    info: AgentInfo,
) -> ModelResponse:
    del messages, info
    return ModelResponse(parts=[TextPart("Finance agent ready.")])


def test_example_main_functions_dispatch_run_acp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[Any, Any]] = []

    def fake_run_acp(*, agent: Any, config: Any) -> None:
        captured.append((agent, config))

    monkeypatch.setattr(finance_agent, "run_acp", fake_run_acp)
    monkeypatch.setattr(travel_agent, "run_acp", fake_run_acp)

    finance_agent.main()
    travel_agent.main()

    assert captured == [
        (finance_agent.agent, finance_agent.config),
        (travel_agent.agent, travel_agent.config),
    ]


def test_finance_example_helpers_cover_workspace_and_plan_paths(tmp_path: Path) -> None:
    finance_root = finance_agent._finance_root(tmp_path)
    finance_agent._ensure_finance_workspace(finance_root)

    assert "watchlist.md" in finance_agent._list_market_files(finance_root)
    assert finance_agent._read_market_note(finance_root, "watchlist.md", max_chars=64).startswith(
        "# Finance Watchlist"
    )
    assert (
        finance_agent._save_market_note(finance_root, "notes/rebalance.md", "trim risk")
        == "Saved `notes/rebalance.md`."
    )
    assert (
        finance_agent._resolve_market_path(finance_root, "notes/rebalance.md").read_text(
            encoding="utf-8"
        )
        == "trim risk"
    )
    with pytest.raises(ValueError, match="finance workspace"):
        finance_agent._resolve_market_path(finance_root, "../escape.md")
    with pytest.raises(ValueError, match="max_chars must be positive"):
        finance_agent._read_market_note(finance_root, "watchlist.md", max_chars=0)
    with pytest.raises(ValueError, match="File not found"):
        finance_agent._read_market_note(finance_root, "missing.md", max_chars=32)
    assert finance_agent._render_plan_snapshot(entries=[], plan_markdown=None) == (
        "No finance plan has been recorded yet."
    )
    rendered_plan = finance_agent._render_plan_snapshot(
        entries=[
            PlanEntry(content="Review the watchlist", priority="high", status="pending"),
            PlanEntry(content="Write the trade note", priority="medium", status="pending"),
        ],
        plan_markdown="# Finance Plan",
    )
    assert "Current finance plan entries:" in rendered_plan
    assert "1. [pending] (high) Review the watchlist" in rendered_plan
    assert "Current finance plan entries:" in finance_agent._render_plan_snapshot(
        entries=[PlanEntry(content="Check liquidity", priority="low", status="pending")],
        plan_markdown=None,
    )


def test_finance_example_uses_env_override_and_raw_module_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACP_FINANCE_MODEL", "openai:gpt-5.4-mini")
    assert finance_agent._workspace_model_name() == "openai:gpt-5.4-mini"
    assert finance_agent.agent.name == "finance-agent"
    assert finance_agent.config.capability_bridges is not None
    assert [bridge.__class__.__name__ for bridge in finance_agent.config.capability_bridges] == [
        "ThinkingBridge",
        "PrepareToolsBridge",
    ]


def test_finance_example_tools_and_adapter_cover_runtime_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    available_tools = cast(
        list[Any],
        [
            type("ToolDef", (), {"name": finance_agent._READ_NOTE_TOOL})(),
            type("ToolDef", (), {"name": finance_agent._WRITE_NOTE_TOOL})(),
        ],
    )
    assert [
        tool.name
        for tool in finance_agent._read_only_tools(
            cast(Any, object()),
            available_tools,
        )
    ] == [finance_agent._READ_NOTE_TOOL]
    assert finance_agent._trade_tools(cast(Any, object()), available_tools) == available_tools

    tools = finance_agent.agent._function_toolset.tools
    describe_tool = cast(Any, tools["describe_finance_surface"])
    watchlist_tool = cast(Any, tools[finance_agent._WATCHLIST_TOOL])
    read_tool = cast(Any, tools[finance_agent._READ_NOTE_TOOL])
    write_tool = cast(Any, tools[finance_agent._WRITE_NOTE_TOOL])
    quote_tool = cast(Any, tools[finance_agent._QUOTE_TOOL])

    assert "structured native plan generation" in describe_tool.function()
    assert "Seeded symbols:" in watchlist_tool.function()
    assert "Finance Watchlist" in read_tool.function("watchlist.md", 128)
    assert quote_tool.function("nvda") == "NVDA 118.42 USD | bias: high-volatility"
    assert write_tool.function("notes/research.md", "buy quality") == "Saved `notes/research.md`."

    with pytest.raises(ValueError, match="Unknown demo symbol"):
        quote_tool.function("tsla")

    session = AcpSessionContext(
        session_id="finance-session",
        cwd=tmp_path,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    provider = cast(Any, finance_agent.config.native_plan_persistence_provider)
    provider.persist_plan_state(
        session,
        finance_agent.agent,
        [PlanEntry(content="Trim leverage", priority="high", status="pending")],
        "# Trade Plan",
    )
    persisted = (tmp_path / ".acpkit" / "plans" / "finance-session.md").read_text(encoding="utf-8")
    assert "# Trade Plan" in persisted
    assert "Trim leverage" in persisted

    client = RecordingClient()
    adapter = create_acp_agent(agent=finance_agent.agent, config=finance_agent.config)
    client = RecordingClient()
    adapter.on_connect(client)
    original_model = finance_agent.agent.model
    monkeypatch.setattr(
        finance_agent.agent,
        "model",
        FunctionModel(_finance_text_model, model_name="finance-example-model"),
    )
    try:
        response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
        asyncio.run(
            adapter.prompt(
                prompt=[text_block("Describe the finance surface.")],
                session_id=response.session_id,
            )
        )
    finally:
        monkeypatch.setattr(finance_agent.agent, "model", original_model)
    assert agent_message_texts(client) == ["Finance agent ready."]


def test_travel_example_helpers_cover_workspace_and_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(travel_agent, "_TRAVEL_ROOT", tmp_path / ".travel-agent")
    travel_agent._ensure_travel_workspace()

    assert travel_agent._truncate_text("hello", limit=10) == "hello"
    assert "...[truncated]" in travel_agent._truncate_text("abcdefghij", limit=4)
    assert "itinerary.md" in travel_agent.list_trip_files()
    assert "Hooks capability introspection" in travel_agent.describe_travel_surface()
    assert "Travel Brief" in travel_agent.read_trip_file("itinerary.md", max_chars=64)
    assert travel_agent.write_trip_file("scratch.txt", "hello") == "Wrote `scratch.txt`."
    assert (tmp_path / ".travel-agent" / "scratch.txt").read_text(encoding="utf-8") == "hello"

    with pytest.raises(ValueError, match="travel demo workspace"):
        travel_agent._resolve_trip_path("../escape.txt")
    with pytest.raises(ValueError, match="max_chars must be positive"):
        travel_agent.read_trip_file("itinerary.md", max_chars=0)
    with pytest.raises(ValueError, match="File not found"):
        travel_agent.read_trip_file("missing.txt")

    assert (
        _resolve_result(
            travel_agent.observe_before_model_request(
                cast(Any, object()),
                request_context=cast(Any, "ctx"),
            )
        )
        == "ctx"
    )
    assert (
        _resolve_result(
            travel_agent.observe_after_model_request(
                cast(Any, object()),
                request_context=cast(Any, "ctx"),
                response=cast(Any, "response"),
            )
        )
        == "response"
    )
    assert _resolve_result(
        travel_agent.observe_read_tool(
            cast(Any, object()),
            call=cast(Any, object()),
            tool_def=cast(Any, object()),
            args={"path": "itinerary.md"},
        )
    ) == {"path": "itinerary.md"}
    assert _resolve_result(
        travel_agent.observe_write_tool(
            cast(Any, object()),
            call=cast(Any, object()),
            tool_def=cast(Any, object()),
            args={"path": "scratch.txt"},
        )
    ) == {"path": "scratch.txt"}
    assert (
        _resolve_result(
            travel_agent.observe_write_result(
                cast(Any, object()),
                call=cast(Any, object()),
                tool_def=cast(Any, object()),
                args={"path": "scratch.txt"},
                result="ok",
            )
        )
        == "ok"
    )


def test_travel_prompt_model_provider_covers_media_override_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACP_TRAVEL_MEDIA_MODEL", "openai:gpt-4.1-mini")

    provider = travel_agent.TravelPromptModelProvider()
    binary_prompt = [
        ResourceContentBlock(
            type="resource_link",
            name="ticket",
            uri="file:///ticket.pdf",
            mime_type="application/pdf",
        ),
        EmbeddedResourceContentBlock(
            type="resource",
            resource=BlobResourceContents(
                uri="resource://boarding-pass.png",
                blob="aGVsbG8=",
                mime_type="image/png",
            ),
        ),
        EmbeddedResourceContentBlock(
            type="resource",
            resource=TextResourceContents(
                uri="resource://note.txt",
                text="hello",
                mime_type="text/plain",
            ),
        ),
    ]

    assert travel_agent._configured_media_model_name() == "openai:gpt-4.1-mini"
    assert travel_agent._prompt_has_binary_media(binary_prompt) is True
    assert travel_agent._prompt_has_image_media(binary_prompt) is True
    assert travel_agent._prompt_has_binary_media([text_block("hello")]) is False
    assert (
        travel_agent._prompt_has_binary_media(
            [
                ResourceContentBlock(
                    type="resource_link",
                    name="notes",
                    uri="file:///notes.txt",
                    mime_type="text/plain",
                )
            ]
        )
        is False
    )
    assert (
        travel_agent._prompt_has_binary_media(
            [
                EmbeddedResourceContentBlock(
                    type="resource",
                    resource=BlobResourceContents(
                        uri="resource://photo.jpg",
                        blob="aGVsbG8=",
                        mime_type="image/jpeg",
                    ),
                )
            ]
        )
        is True
    )
    assert (
        travel_agent._prompt_has_image_media(
            [
                ResourceContentBlock(
                    type="resource_link",
                    name="cover",
                    uri="file:///cover.png",
                    mime_type="image/png",
                )
            ]
        )
        is True
    )

    image_override = provider.get_prompt_model_override(
        cast(Any, object()),
        cast(Any, object()),
        prompt=[
            text_block("describe this hotel"),
            ImageContentBlock(type="image", data="aGVsbG8=", mime_type="image/png"),
        ],
        model_override="openrouter:google/gemini-3-flash-preview",
    )
    audio_override = provider.get_prompt_model_override(
        cast(Any, object()),
        cast(Any, object()),
        prompt=[
            text_block("transcribe this"),
            AudioContentBlock(type="audio", data="aGVsbG8=", mime_type="audio/wav"),
        ],
        model_override=None,
    )

    assert image_override == "openai:gpt-4.1-mini"
    assert audio_override == "openai:gpt-4.1-mini"

    monkeypatch.delenv("ACP_TRAVEL_MEDIA_MODEL", raising=False)
    same_override = provider.get_prompt_model_override(
        cast(Any, object()),
        cast(Any, object()),
        prompt=[text_block("plain text only")],
        model_override="existing-model",
    )
    missing_media_override = provider.get_prompt_model_override(
        cast(Any, object()),
        cast(Any, object()),
        prompt=[
            AudioContentBlock(type="audio", data="aGVsbG8=", mime_type="audio/wav"),
        ],
        model_override=None,
    )

    assert same_override == "existing-model"
    assert missing_media_override is None


def test_travel_model_helpers_cover_env_and_config_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_NAME", "openai:gpt-4.1-mini")
    monkeypatch.delenv("ACP_TRAVEL_MEDIA_MODEL", raising=False)
    monkeypatch.delenv("TRAVEL_MEDIA_MODEL", raising=False)
    assert travel_agent._default_model_name() == "openai:gpt-4.1-mini"
    assert travel_agent._configured_media_model_name() is None
    assert travel_agent.agent.name == "travel-agent"
    assert travel_agent.config.prompt_model_override_provider is not None
    assert travel_agent.config.hook_projection_map is not None


def test_travel_model_helpers_cover_default_model_and_absolute_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(travel_agent, "_TRAVEL_ROOT", tmp_path / ".travel-agent")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.delenv("ACP_TRAVEL_MEDIA_MODEL", raising=False)
    monkeypatch.setenv("TRAVEL_MEDIA_MODEL", "openai:gpt-4.1-nano")

    default_model = travel_agent._default_model_name()
    assert isinstance(default_model, TestModel)
    assert travel_agent._configured_media_model_name() == "openai:gpt-4.1-nano"

    travel_agent._ensure_travel_workspace()
    itinerary_path = (tmp_path / ".travel-agent" / "itinerary.md").resolve()
    scratch_path = (tmp_path / ".travel-agent" / "absolute.txt").resolve()

    assert "Travel Brief" in travel_agent.read_trip_file(str(itinerary_path))
    assert (
        travel_agent.write_trip_file(str(scratch_path), "absolute-path") == "Wrote `absolute.txt`."
    )
    assert scratch_path.read_text(encoding="utf-8") == "absolute-path"
    assert (
        travel_agent._prompt_has_image_media(
            [
                AudioContentBlock(type="audio", data="aGVsbG8=", mime_type="audio/wav"),
                EmbeddedResourceContentBlock(
                    type="resource",
                    resource=BlobResourceContents(
                        uri="resource://audio.wav",
                        blob="aGVsbG8=",
                        mime_type="audio/wav",
                    ),
                ),
            ]
        )
        is False
    )
