from __future__ import annotations as _annotations

import runpy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from langchain_acp import AcpSessionContext
from langchain_acp.session import utc_now

from examples.langchain import codex_graph, deepagents_graph, workspace_graph


def test_langchain_example_main_dispatches_run_acp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[Any, Any]] = []

    def fake_run_acp(*, graph_factory: Any, config: Any) -> None:
        captured.append((graph_factory, config))

    monkeypatch.setattr(workspace_graph, "run_acp", fake_run_acp)
    workspace_graph.main()

    assert captured == [(workspace_graph.graph_from_session, workspace_graph.config)]


def test_codex_langchain_example_builds_graph_from_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_create_codex_chat_openai(model_name: str) -> str:
        captured["model_name"] = model_name
        return "codex-model"

    def fake_create_agent(*, model: Any, tools: list[Any], name: str) -> object:
        captured["model"] = model
        captured["tools"] = tools
        captured["name"] = name
        return object()

    monkeypatch.setattr(codex_graph, "create_codex_chat_openai", fake_create_codex_chat_openai)
    monkeypatch.setattr(codex_graph, "create_agent", fake_create_agent)

    graph = codex_graph.build_graph()

    assert graph is not None
    assert captured["model_name"] == codex_graph.MODEL_NAME
    assert captured["model"] == "codex-model"
    assert captured["name"] == "codex-graph"
    assert [tool.__name__ for tool in captured["tools"]] == ["describe_codex_surface"]
    assert "Codex graph features:" in codex_graph.describe_codex_surface()


def test_codex_langchain_example_main_dispatches_run_acp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[Any, Any]] = []

    monkeypatch.setattr(codex_graph, "build_graph", lambda: "graph-object")

    def fake_run_acp(*, graph: Any, config: Any) -> None:
        captured.append((graph, config))

    monkeypatch.setattr(codex_graph, "run_acp", fake_run_acp)

    codex_graph.main()

    assert captured == [("graph-object", codex_graph.config)]


def test_codex_langchain_example_module_runs_as_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    import codex_auth_helper
    import langchain.agents
    import langchain_acp

    monkeypatch.setattr(codex_auth_helper, "create_codex_chat_openai", lambda _: "codex-model")
    monkeypatch.setattr(
        langchain.agents,
        "create_agent",
        lambda *, model, tools, name: {
            "model": model,
            "tools": tools,
            "name": name,
        },
    )

    def fake_run_acp(*, graph: Any, config: Any) -> None:
        observed["call"] = (graph, config)

    monkeypatch.setattr(langchain_acp, "run_acp", fake_run_acp)

    runpy.run_module("examples.langchain.codex_graph", run_name="__main__")

    graph, config = observed["call"]
    assert graph["model"] == "codex-model"
    assert graph["name"] == "codex-graph"
    assert config is not None


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


def test_langchain_example_workspace_bound_tools_cover_private_closures(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".workspace-graph"
    root.mkdir(parents=True, exist_ok=True)
    tools = {cast(Any, tool).__name__: tool for tool in workspace_graph._bind_workspace_tools(root)}

    assert tools["describe_workspace_surface"]() == workspace_graph.describe_workspace_surface()
    assert tools["list_workspace_files"]() == ""
    assert cast(Any, tools["read_workspace_note"]).__name__ == "read_workspace_note"
    assert cast(Any, tools["write_workspace_note"]).__name__ == "write_workspace_note"

    assert tools["write_workspace_note"]("note.md", "# Demo") == "Wrote `note.md`."
    assert tools["list_workspace_files"]() == "README.md\nnote.md"
    assert tools["read_workspace_note"]("note.md") == "# Demo"

    with pytest.raises(ValueError, match="File not found"):
        tools["read_workspace_note"]("missing.md")


def test_langchain_example_workspace_module_runs_as_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    import langchain_acp

    def fake_run_acp(*, graph_factory: Any, config: Any) -> None:
        observed["call"] = (graph_factory, config)

    monkeypatch.setattr(langchain_acp, "run_acp", fake_run_acp)

    runpy.run_module("examples.langchain.workspace_graph", run_name="__main__")

    graph_factory, config = observed["call"]
    assert graph_factory.__name__ == "graph_from_session"
    assert config is not None


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
    assert (
        deepagents_graph._resolve_workspace_path("itinerary.md", root=root)
        == (root / "itinerary.md").resolve()
    )

    with pytest.raises(ValueError, match="DeepAgents example workspace"):
        deepagents_graph._resolve_workspace_path("../escape.md")

    with pytest.raises(ValueError, match="File not found"):
        deepagents_graph.read_file("missing.md")

    assert deepagents_graph.config.projection_maps


def test_deepagents_example_graph_factory_requires_optional_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AcpSessionContext(
        session_id="example-session",
        cwd=Path.cwd(),
        created_at=utc_now(),
        updated_at=utc_now(),
    )

    monkeypatch.setattr(deepagents_graph, "_deepagents_available", lambda: False)

    with pytest.raises(RuntimeError, match="langchain-acp\\[deepagents\\]"):
        deepagents_graph.graph_from_session(session)


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


def test_deepagents_example_graph_factory_builds_graph_when_dependency_is_mocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_graph = object()
    captured: dict[str, Any] = {}

    def fake_create_deep_agent(**kwargs: Any) -> object:
        captured.update(kwargs)
        return fake_graph

    monkeypatch.setattr(deepagents_graph, "_deepagents_available", lambda: True)
    monkeypatch.setattr(
        deepagents_graph,
        "import_module",
        lambda name: cast(Any, SimpleNamespace(create_deep_agent=fake_create_deep_agent)),
    )

    session = AcpSessionContext(
        session_id="example-session",
        cwd=tmp_path,
        created_at=utc_now(),
        updated_at=utc_now(),
    )

    assert deepagents_graph.graph_from_session(session) is fake_graph
    tool_names = {tool.__name__ for tool in cast(list[Any], captured["tools"])}
    assert tool_names == {"list_workspace_files", "read_file", "write_file"}


def test_deepagents_example_seed_session_and_module_run_as_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / ".deepagents-graph"
    monkeypatch.setattr(deepagents_graph, "WORKSPACE_ROOT", root)

    session = deepagents_graph._seed_session()
    assert session.session_id == "deepagents-example"
    assert session.cwd == root.resolve()

    observed: dict[str, Any] = {}

    import langchain_acp

    def fake_run_acp(*, graph_factory: Any, config: Any) -> None:
        observed["call"] = (graph_factory, config)

    monkeypatch.setattr(langchain_acp, "run_acp", fake_run_acp)
    runpy.run_module("examples.langchain.deepagents_graph", run_name="__main__")

    graph_factory, config = observed["call"]
    assert graph_factory.__name__ == "graph_from_session"
    assert config is not None
