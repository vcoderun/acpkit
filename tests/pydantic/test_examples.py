from __future__ import annotations as _annotations

import asyncio
from typing import Any, cast

import pytest
from acp.schema import AudioContentBlock, ImageContentBlock, PlanEntry
from pydantic_ai.models.test import TestModel

from examples.pydantic import approvals, bridges, factory_agent, providers, static_agent
from examples.pydantic import strong_agent as workspace_example
from examples.pydantic import strong_agent_v2 as media_workspace_example

from .support import RecordingClient, agent_message_texts, text_block


@pytest.mark.parametrize(
    ("module", "expected_keys"),
    [
        (static_agent, {"agent"}),
        (factory_agent, {"agent_factory", "config"}),
        (providers, {"agent", "config"}),
        (approvals, {"agent", "config"}),
        (bridges, {"agent_factory", "config"}),
    ],
)
def test_example_main_functions_dispatch_run_acp(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    expected_keys: set[str],
) -> None:
    captured: dict[str, Any] = {}

    def fake_run_acp(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(module, "run_acp", fake_run_acp)

    module.main()

    assert set(captured) == expected_keys
    for value in captured.values():
        assert value is not None


@pytest.mark.parametrize(
    "module",
    [workspace_example, media_workspace_example],
)
def test_workspace_example_mains_dispatch_run_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    module: Any,
) -> None:
    captured: list[Any] = []

    async def fake_run_agent(agent: Any) -> None:
        captured.append(agent)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(module, "run_agent", fake_run_agent)

    module.main()

    assert len(captured) == 1


def test_workspace_example_repo_helpers_and_plan_snapshot(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")

    assert "src/app.py" in workspace_example._search_repo_paths(repo_root, "app")
    assert "Top-level paths:" in workspace_example._search_repo_paths(repo_root, "")
    assert (
        workspace_example._read_repo_file(repo_root, "src/app.py", max_chars=64)
        == "print('hello')\n"
    )

    rendered = workspace_example._render_plan_snapshot(
        entries=[
            PlanEntry(content="Inspect the repo", priority="high", status="pending"),
            PlanEntry(content="Write the patch", priority="medium", status="pending"),
        ],
        plan_markdown="# Plan",
    )

    assert "Current ACP plan entries:" in rendered
    assert "1. [pending] (high) Inspect the repo" in rendered


def test_workspace_example_build_server_agent_handles_sessions(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    adapter = workspace_example.build_server_agent()
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Describe the workspace surface.")],
            session_id=session.session_id,
        )
    )

    assert agent_message_texts(client) == ["Workspace example ready."]


def test_media_workspace_prompt_override_provider_covers_binary_and_image_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_NAME", "openrouter:google/gemini-3-flash-preview")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("ACP_MEDIA_MODEL", "openai:gpt-4.1-mini")

    provider = media_workspace_example.WorkspacePromptModelProvider()
    session = cast(Any, object())
    agent = cast(Any, object())

    image_override = provider.get_prompt_model_override(
        session,
        agent,
        prompt=[
            text_block("describe the image"),
            ImageContentBlock(type="image", data="aGVsbG8=", mime_type="image/png"),
        ],
        model_override="openrouter:google/gemini-3-flash-preview",
    )
    audio_override = provider.get_prompt_model_override(
        session,
        agent,
        prompt=[
            text_block("transcribe this"),
            AudioContentBlock(type="audio", data="aGVsbG8=", mime_type="audio/wav"),
        ],
        model_override=None,
    )

    assert image_override == "google-gla:gemini-3-flash-preview"
    assert audio_override == "openai:gpt-4.1-mini"
    assert media_workspace_example._google_media_fallback_model_name("openai:gpt-5.4") is None


def test_media_workspace_example_build_server_agent_handles_media_prompt(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    adapter = media_workspace_example.build_server_agent()
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[
                text_block("Describe this image."),
                ImageContentBlock(type="image", data="aGVsbG8=", mime_type="image/png"),
            ],
            session_id=session.session_id,
        )
    )

    assert agent_message_texts(client) == ["Media routing example ready."]


def test_factory_example_chooses_model_from_workspace_name(tmp_path) -> None:
    review_session = cast(Any, type("Session", (), {"cwd": tmp_path / "review"})())
    fast_session = cast(Any, type("Session", (), {"cwd": tmp_path / "chat"})())

    review_agent = factory_agent.build_agent(review_session)
    fast_agent = factory_agent.build_agent(fast_session)

    assert isinstance(review_agent.model, TestModel)
    assert isinstance(fast_agent.model, TestModel)
    assert review_agent.name == "factory-review"
    assert fast_agent.name == "factory-chat"
