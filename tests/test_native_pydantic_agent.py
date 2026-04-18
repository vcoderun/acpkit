from __future__ import annotations as _annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import Any, cast

from pydantic_ai import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from .pydantic.support import (
    FileEditToolCallContent,
    RecordingClient,
    ToolCallProgress,
    ToolCallStart,
    create_acp_agent,
    text_block,
)


def _load_demo_module():
    module_path = Path(__file__).resolve().parents[1] / "examples" / "pydantic" / "travel_agent.py"
    spec = importlib.util.spec_from_file_location("travel_agent_demo", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _latest_user_prompt(messages: list[ModelRequest | ModelResponse]) -> str:
    for message in reversed(messages):
        if not isinstance(message, ModelRequest):
            continue
        for part in reversed(message.parts):
            if isinstance(part, UserPromptPart):
                content = part.content
                if isinstance(content, str):
                    return content
    return ""


def _travel_demo_model(
    messages: list[ModelRequest | ModelResponse], info: AgentInfo
) -> ModelResponse:
    del info
    latest_message = messages[-1]
    if isinstance(latest_message, ModelRequest):
        for part in latest_message.parts:
            if isinstance(part, ToolReturnPart):
                return ModelResponse(parts=[TextPart(f"{part.tool_name}: {part.content}")])

    prompt = _latest_user_prompt(messages)
    if prompt == "read trip file itinerary.md":
        return ModelResponse(
            parts=[ToolCallPart("read_trip_file", {"path": "itinerary.md", "max_chars": 4000})]
        )
    if prompt == "write trip file scratch.txt: hello from the native demo":
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "write_trip_file",
                    {"path": "scratch.txt", "content": "hello from the native demo"},
                )
            ]
        )
    return ModelResponse(parts=[TextPart("Travel demo mode is active.")])


demo = _load_demo_module()


def test_native_pydantic_agent_read_prompt_emits_hook_and_diff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(demo, "_TRAVEL_ROOT", tmp_path / "native-demo")
    demo._ensure_travel_workspace()
    monkeypatch.setattr(
        demo.agent,
        "model",
        FunctionModel(_travel_demo_model, model_name="travel-demo-model"),
    )

    adapter = create_acp_agent(agent=demo.agent, config=demo.config)
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("read trip file itinerary.md")],
            session_id=session.session_id,
        )
    )

    hook_titles = [
        update.title for _, update in client.updates if isinstance(update, ToolCallStart)
    ]
    assert "Hook Before Model (observe_before_model_request)" in hook_titles

    read_updates = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallProgress) and update.title == "read_trip_file"
    ]
    assert len(read_updates) == 1
    assert read_updates[0].content is not None
    diff = read_updates[0].content[0]
    assert isinstance(diff, FileEditToolCallContent)
    assert diff.path.endswith("itinerary.md")
    assert diff.old_text == ""
    assert "Travel Brief" in diff.new_text


def test_native_pydantic_agent_write_prompt_emits_hook_and_diff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(demo, "_TRAVEL_ROOT", tmp_path / "native-demo")
    demo._ensure_travel_workspace()
    monkeypatch.setattr(
        demo.agent,
        "model",
        FunctionModel(_travel_demo_model, model_name="travel-demo-model"),
    )

    adapter = create_acp_agent(agent=demo.agent, config=demo.config)
    client = RecordingClient()
    client.queue_permission_selected("allow_once")
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("write trip file scratch.txt: hello from the native demo")],
            session_id=session.session_id,
        )
    )

    write_start = next(
        cast(Any, update)
        for _, update in client.updates
        if isinstance(update, ToolCallStart) and update.title == "write_trip_file"
    )
    assert write_start.content is not None
    diff = write_start.content[0]
    assert isinstance(diff, FileEditToolCallContent)
    assert diff.path.endswith("scratch.txt")
    assert diff.old_text == ""
    assert diff.new_text == "hello from the native demo"

    hook_titles = [
        update.title for _, update in client.updates if isinstance(update, ToolCallStart)
    ]
    assert "Hook Before Execute [write_trip_file] (observe_write_tool)" in hook_titles
    assert (tmp_path / "native-demo" / "scratch.txt").read_text(encoding="utf-8") == (
        "hello from the native demo"
    )
