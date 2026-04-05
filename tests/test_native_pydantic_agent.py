from __future__ import annotations as _annotations

import asyncio
import importlib.util
from pathlib import Path

from .pydantic.support import (
    AdapterConfig,
    FileEditToolCallContent,
    FileSystemProjectionMap,
    MemorySessionStore,
    RecordingClient,
    ToolCallProgress,
    ToolCallStart,
    create_acp_agent,
    text_block,
)


def _load_demo_module():
    module_path = (
        Path(__file__).resolve().parents[1] / "examples" / "pydantic" / "hook_projection.py"
    )
    spec = importlib.util.spec_from_file_location("hook_projection_demo", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo = _load_demo_module()


def test_native_pydantic_agent_read_prompt_emits_hook_and_diff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(demo, "_DEMO_ROOT", tmp_path / "native-demo")
    demo._ensure_demo_workspace()

    adapter = create_acp_agent(
        agent=demo.agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
        projection_maps=(
            FileSystemProjectionMap(
                default_read_tool="read_demo_file",
                default_write_tool="write_demo_file",
            ),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("read demo file status.md")],
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
        if isinstance(update, ToolCallProgress) and update.title == "read_demo_file"
    ]
    assert len(read_updates) == 1
    assert read_updates[0].content is not None
    diff = read_updates[0].content[0]
    assert isinstance(diff, FileEditToolCallContent)
    assert diff.path.endswith("status.md")
    assert diff.old_text == ""
    assert "Hook Projection Demo" in diff.new_text


def test_native_pydantic_agent_write_prompt_emits_hook_and_diff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(demo, "_DEMO_ROOT", tmp_path / "native-demo")
    demo._ensure_demo_workspace()

    adapter = create_acp_agent(
        agent=demo.agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
        projection_maps=(
            FileSystemProjectionMap(
                default_read_tool="read_demo_file",
                default_write_tool="write_demo_file",
            ),
        ),
    )
    client = RecordingClient()
    client.queue_permission_selected("allow_once")
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("write demo file scratch.txt: hello from the native demo")],
            session_id=session.session_id,
        )
    )

    write_start = next(
        update
        for _, update in client.updates
        if isinstance(update, ToolCallStart) and update.title == "write_demo_file"
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
    assert "Hook Before Tool [write_demo_file] (observe_write_tool)" in hook_titles
    assert (tmp_path / "native-demo" / "scratch.txt").read_text(encoding="utf-8") == (
        "hello from the native demo"
    )
