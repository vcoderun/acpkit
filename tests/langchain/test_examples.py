from __future__ import annotations as _annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from langchain_acp import AcpSessionContext
from langchain_acp.session import utc_now

from examples.langchain import deepagents_graph, workspace_graph


def test_langchain_example_main_dispatches_run_acp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[Any, Any]] = []

    def fake_run_acp(*, graph_factory: Any, config: Any) -> None:
        captured.append((graph_factory, config))

    monkeypatch.setattr(workspace_graph, "run_acp", fake_run_acp)
    workspace_graph.main()

    assert captured == [(workspace_graph.graph_from_session, workspace_graph.config)]


def test_langchain_example_workspace_helpers_cover_seeded_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / ".workspace-graph"
    monkeypatch.setattr(workspace_graph, "WORKSPACE_ROOT", root)

    workspace_graph._ensure_workspace()

    assert workspace_graph.list_workspace_files() == "README.md"
    assert "session-aware `graph_from_session(...)`" in workspace_graph.describe_workspace_surface()
    assert "Workspace Graph Demo" in workspace_graph.read_workspace_note("README.md")
    assert workspace_graph.write_workspace_note("scratch.txt", "# Hello") == "Wrote `scratch.txt`."
    assert workspace_graph.list_workspace_files() == "README.md\nscratch.txt"

    with pytest.raises(ValueError, match="workspace graph demo directory"):
        workspace_graph._resolve_workspace_path("../escape.md")

    with pytest.raises(ValueError, match="File not found"):
        workspace_graph.read_workspace_note("missing.md")

    assert workspace_graph.config.projection_maps


def test_langchain_example_workspace_graph_factory_uses_session_root(
    tmp_path: Path,
) -> None:
    session_root = tmp_path / "remote-workspace"
    session_root.mkdir(parents=True, exist_ok=True)
    session = AcpSessionContext(
        session_id="workspace-example",
        cwd=session_root,
        created_at=utc_now(),
        updated_at=utc_now(),
    )

    graph = workspace_graph.graph_from_session(session)

    assert graph is not None
    seeded_root = session_root / ".workspace-graph"
    assert (seeded_root / "README.md").exists()


def test_deepagents_example_main_dispatches_run_acp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[Any, Any]] = []

    def fake_run_acp(*, graph_factory: Any, config: Any) -> None:
        captured.append((graph_factory, config))

    monkeypatch.setattr(deepagents_graph, "run_acp", fake_run_acp)
    deepagents_graph.main()

    assert captured == [(deepagents_graph.graph_from_session, deepagents_graph.config)]


def test_deepagents_example_workspace_helpers_cover_seeded_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / ".deepagents-graph"
    monkeypatch.setattr(deepagents_graph, "WORKSPACE_ROOT", root)

    deepagents_graph._ensure_workspace()

    assert deepagents_graph.list_workspace_files() == "brief.md"
    assert "DeepAgents Demo" in deepagents_graph.read_file("brief.md")
    assert deepagents_graph.write_file("itinerary.md", "# Trip") == "Wrote itinerary.md"
    assert deepagents_graph.list_workspace_files() == "brief.md\nitinerary.md"
    assert deepagents_graph.read_file("itinerary.md") == "# Trip"

    with pytest.raises(ValueError, match="DeepAgents example workspace"):
        deepagents_graph._resolve_workspace_path("../escape.md")

    with pytest.raises(ValueError, match="File not found"):
        deepagents_graph.read_file("missing.md")

    assert deepagents_graph.config.projection_maps


def test_deepagents_example_graph_factory_requires_optional_dependency() -> None:
    session = AcpSessionContext(
        session_id="example-session",
        cwd=Path.cwd(),
        created_at=utc_now(),
        updated_at=utc_now(),
    )

    if not deepagents_graph._deepagents_available():
        with pytest.raises(RuntimeError, match="langchain-acp\\[deepagents\\]"):
            deepagents_graph.graph_from_session(session)
        return

    graph = deepagents_graph.graph_from_session(session)
    assert graph is not None


def test_deepagents_example_graph_factory_builds_graph_from_lazy_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / ".deepagents-graph"
    monkeypatch.setattr(deepagents_graph, "WORKSPACE_ROOT", root)
    monkeypatch.setattr(deepagents_graph, "_deepagents_available", lambda: True)

    captured: dict[str, Any] = {}

    def fake_create_deep_agent(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        deepagents_graph,
        "import_module",
        lambda name: cast(Any, SimpleNamespace(create_deep_agent=fake_create_deep_agent)),
    )

    session = AcpSessionContext(
        session_id="example-session",
        cwd=root,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    graph = deepagents_graph.graph_from_session(session)

    assert graph is not None
    assert captured["interrupt_on"] == {"write_file": True}
    assert captured["name"] == "deepagents-.deepagents-graph"
    tool_names = {tool.__name__ for tool in cast(list[Any], captured["tools"])}
    assert tool_names == {"list_workspace_files", "read_file", "write_file"}
