from __future__ import annotations as _annotations

import importlib
import importlib.util
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage

from acpkit import MissingAdapterError, load_target, run_target
from acpkit.adapters import find_matching_adapter, is_langchain_target

from .langchain.support import GenericFakeChatModel


def _write_module(tmp_path: Path, module_name: str, source: str) -> None:
    (tmp_path / f"{module_name}.py").write_text(source, encoding="utf-8")


def test_root_adapter_detects_langchain_graph_targets() -> None:
    def read_file(path: str) -> str:
        """Read a file from the workspace."""
        return f"contents:{path}"

    graph = create_agent(
        model=GenericFakeChatModel(messages=iter([AIMessage(content="ready")])),
        tools=[read_file],
        name="langchain-demo",
    )

    adapter = find_matching_adapter(graph)

    assert is_langchain_target(graph)
    assert adapter is not None
    assert adapter.adapter_id == "langchain"


def test_load_target_resolves_langchain_graph_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_module(
        tmp_path,
        "sample_langchain_graph_app",
        "\n".join(
            (
                "from itertools import cycle",
                "from langchain.agents import create_agent",
                "from langchain_core.language_models import GenericFakeChatModel",
                "from langchain_core.messages import AIMessage",
                "",
                "def read_file(path: str) -> str:",
                '    """Read a file from the workspace."""',
                '    return f"contents:{path}"',
                "",
                "graph = create_agent(",
                '    model=GenericFakeChatModel(messages=cycle([AIMessage(content="ready")])),',
                "    tools=[read_file],",
                '    name="langchain-demo",',
                ")",
            )
        ),
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    loaded_target = load_target("sample_langchain_graph_app:graph")

    assert is_langchain_target(loaded_target)


def test_load_target_uses_latest_langchain_graph_when_attribute_is_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_module(
        tmp_path,
        "sample_latest_langchain_graph",
        "\n".join(
            (
                "from itertools import cycle",
                "from langchain.agents import create_agent",
                "from langchain_core.language_models import GenericFakeChatModel",
                "from langchain_core.messages import AIMessage",
                "",
                "def read_file(path: str) -> str:",
                '    """Read a file from the workspace."""',
                '    return f"contents:{path}"',
                "",
                "first_graph = create_agent(",
                '    model=GenericFakeChatModel(messages=cycle([AIMessage(content="first")])),',
                "    tools=[read_file],",
                '    name="first-graph",',
                ")",
                "second_graph = create_agent(",
                '    model=GenericFakeChatModel(messages=cycle([AIMessage(content="second")])),',
                "    tools=[read_file],",
                '    name="second-graph",',
                ")",
            )
        ),
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    loaded_target = load_target("sample_latest_langchain_graph")

    assert is_langchain_target(loaded_target)
    assert loaded_target is load_target("sample_latest_langchain_graph:second_graph")


def test_run_target_dispatches_langchain_graph_through_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_module(
        tmp_path,
        "sample_run_langchain_graph",
        "\n".join(
            (
                "from itertools import cycle",
                "from langchain.agents import create_agent",
                "from langchain_core.language_models import GenericFakeChatModel",
                "from langchain_core.messages import AIMessage",
                "",
                "def read_file(path: str) -> str:",
                '    """Read a file from the workspace."""',
                '    return f"contents:{path}"',
                "",
                "graph = create_agent(",
                '    model=GenericFakeChatModel(messages=cycle([AIMessage(content="run")])),',
                "    tools=[read_file],",
                '    name="run-graph",',
                ")",
            )
        ),
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    captured_graphs: list[Any] = []
    original_import_module = importlib.import_module

    def fake_import_module(name: str, package: str | None = None) -> ModuleType:
        if name == "langchain_acp":
            return cast(
                ModuleType,
                SimpleNamespace(run_acp=lambda *, graph: captured_graphs.append(graph)),
            )
        return original_import_module(name, package)

    monkeypatch.setattr("acpkit.adapters.importlib.import_module", fake_import_module)

    run_target("sample_run_langchain_graph:graph")

    assert len(captured_graphs) == 1
    assert is_langchain_target(captured_graphs[0])


def test_load_target_reports_missing_langchain_adapter_from_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_import_module(name: str, package: str | None = None) -> ModuleType:
        del package
        if name == "sample_missing_langchain_dependency":
            raise ModuleNotFoundError(
                "No module named 'langgraph'",
                name="langgraph",
            )
        return importlib.util.module_from_spec(ModuleSpec(name, loader=None))

    monkeypatch.setattr("acpkit.runtime.importlib.import_module", fake_import_module)
    monkeypatch.setattr(
        "acpkit.adapters.find_spec",
        lambda name: (
            None
            if name in {"langchain", "langgraph", "langchain_acp"}
            else importlib.util.find_spec(name)
        ),
    )

    with pytest.raises(MissingAdapterError) as exc_info:
        load_target("sample_missing_langchain_dependency:graph")

    assert 'uv pip install "acpkit[langchain]"' in str(exc_info.value)
