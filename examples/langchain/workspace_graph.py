from __future__ import annotations as _annotations

from collections.abc import Callable
from itertools import cycle
from pathlib import Path

from langchain.agents import create_agent
from langchain_acp import (
    AcpSessionContext,
    AdapterConfig,
    CompiledAgentGraph,
    FileSystemProjectionMap,
    MemorySessionStore,
    run_acp,
)
from langchain_core.language_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

__all__ = (
    "WORKSPACE_ROOT",
    "config",
    "describe_workspace_surface",
    "graph",
    "graph_from_session",
    "list_workspace_files",
    "main",
    "read_workspace_note",
    "write_workspace_note",
)

WORKSPACE_ROOT = Path(__file__).with_name(".workspace-graph")
_READ_TOOL = "read_workspace_note"
_WRITE_TOOL = "write_workspace_note"
_SESSION_ROOT_NAME = ".workspace-graph"


def _ensure_workspace(root: Path | None = None) -> Path:
    if root is None:
        root = WORKSPACE_ROOT
    root.mkdir(parents=True, exist_ok=True)
    readme_path = root / "README.md"
    if not readme_path.exists():
        readme_path.write_text(
            "# Workspace Graph Demo\n\n"
            "This seeded file lets ACP render a read diff through the LangChain example.\n",
            encoding="utf-8",
        )
    return root


def _resolve_workspace_path(path: str, *, root: Path | None = None) -> Path:
    if root is None:
        root = WORKSPACE_ROOT
    workspace_root = _ensure_workspace(root).resolve()
    candidate = (workspace_root / path).resolve()
    try:
        candidate.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError("Path must stay inside the workspace graph demo directory.") from exc
    return candidate


def _write_workspace_note(root: Path, path: str, content: str) -> str:
    note_path = _resolve_workspace_path(path, root=root)
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(content, encoding="utf-8")
    return f"Wrote `{note_path.relative_to(root).as_posix()}`."


def _workspace_surface_summary() -> str:
    return "\n".join(
        (
            "Workspace graph features:",
            "- module-level `graph` for direct `acpkit run ...:graph` exposure",
            "- session-aware `graph_from_session(...)` for per-session graph construction",
            "- file read and write projection through `FileSystemProjectionMap`",
            "- a seeded workspace that keeps ACP rendering deterministic",
        )
    )


def list_workspace_files() -> str:
    """List seeded workspace files that the demo graph can read."""

    root = _ensure_workspace()
    files = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())
    return "\n".join(files)


def describe_workspace_surface() -> str:
    """Summarize the ACP-facing features exposed by the workspace graph example."""

    return _workspace_surface_summary()


def read_workspace_note(path: str) -> str:
    """Read a workspace note and return its text content."""

    note_path = _resolve_workspace_path(path)
    if not note_path.exists():
        raise ValueError(f"File not found: {path}")
    return note_path.read_text(encoding="utf-8")


def write_workspace_note(path: str, content: str) -> str:
    """Write a workspace note and return the saved relative path."""

    root = _ensure_workspace()
    return _write_workspace_note(root, path, content)


def _session_workspace_root(session: AcpSessionContext) -> Path:
    return session.cwd.resolve() / _SESSION_ROOT_NAME


def _bind_workspace_tools(root: Path) -> tuple[Callable[..., str], ...]:
    def _describe_workspace_surface() -> str:
        return _workspace_surface_summary()

    _describe_workspace_surface.__name__ = "describe_workspace_surface"
    _describe_workspace_surface.__doc__ = describe_workspace_surface.__doc__

    def _list_workspace_files() -> str:
        files = sorted(
            path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
        )
        return "\n".join(files)

    _list_workspace_files.__name__ = "list_workspace_files"
    _list_workspace_files.__doc__ = list_workspace_files.__doc__

    def _read_workspace_note(path: str) -> str:
        note_path = _resolve_workspace_path(path, root=root)
        if not note_path.exists():
            raise ValueError(f"File not found: {path}")
        return note_path.read_text(encoding="utf-8")

    _read_workspace_note.__name__ = _READ_TOOL
    _read_workspace_note.__doc__ = read_workspace_note.__doc__

    def _write_workspace_note_tool(path: str, content: str) -> str:
        return _write_workspace_note(root, path, content)

    _write_workspace_note_tool.__name__ = _WRITE_TOOL
    _write_workspace_note_tool.__doc__ = write_workspace_note.__doc__
    return (
        _describe_workspace_surface,
        _list_workspace_files,
        _read_workspace_note,
        _write_workspace_note_tool,
    )


def _build_graph(root: Path, *, name: str) -> CompiledAgentGraph:
    _ensure_workspace(root)
    return create_agent(
        model=GenericFakeChatModel(messages=cycle([AIMessage(content="Workspace graph ready.")])),
        tools=list(_bind_workspace_tools(root)),
        name=name,
    )


def graph_from_session(session: AcpSessionContext) -> CompiledAgentGraph:
    root = _ensure_workspace(_session_workspace_root(session)).resolve()
    return _build_graph(root, name=f"workspace-{session.cwd.name}")


graph = _build_graph(_ensure_workspace().resolve(), name="workspace-graph")

config = AdapterConfig(
    session_store=MemorySessionStore(),
    projection_maps=[
        FileSystemProjectionMap(
            read_tool_names=frozenset({_READ_TOOL}),
            write_tool_names=frozenset({_WRITE_TOOL}),
        ),
    ],
)


def main() -> None:
    _ensure_workspace()
    run_acp(graph_factory=graph_from_session, config=config)


if __name__ == "__main__":
    main()
