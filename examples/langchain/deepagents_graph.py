from __future__ import annotations as _annotations

from importlib import import_module
from importlib.util import find_spec
from itertools import cycle
from pathlib import Path
from typing import TYPE_CHECKING

from langchain_acp import (
    AcpSessionContext,
    AdapterConfig,
    DeepAgentsCompatibilityBridge,
    DeepAgentsProjectionMap,
    run_acp,
)
from langchain_acp.session import utc_now
from langchain_core.language_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

if TYPE_CHECKING:
    from langchain_acp import CompiledAgentGraph

__all__ = (
    "WORKSPACE_ROOT",
    "config",
    "graph",
    "graph_from_session",
    "list_workspace_files",
    "main",
    "read_file",
    "write_file",
)

WORKSPACE_ROOT = Path(__file__).with_name(".deepagents-graph")


def _deepagents_available() -> bool:
    return find_spec("deepagents") is not None


def _ensure_workspace(root: Path | None = None) -> Path:
    if root is None:
        root = WORKSPACE_ROOT
    root.mkdir(parents=True, exist_ok=True)
    brief_path = root / "brief.md"
    if not brief_path.exists():
        brief_path.write_text(
            "# DeepAgents Demo\n\n"
            "This seeded file exists so ACP can render read and write projections.\n",
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
        raise ValueError("Path must stay inside the DeepAgents example workspace.") from exc
    return candidate


def list_workspace_files() -> str:
    """List files in the seeded DeepAgents example workspace."""

    root = _ensure_workspace()
    files = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())
    return "\n".join(files)


def read_file(path: str) -> str:
    """Read a file from the DeepAgents example workspace."""

    note_path = _resolve_workspace_path(path)
    if not note_path.exists():
        raise ValueError(f"File not found: {path}")
    return note_path.read_text(encoding="utf-8")


def write_file(path: str, content: str) -> str:
    """Write a file in the DeepAgents example workspace."""

    note_path = _resolve_workspace_path(path)
    note_path.write_text(content, encoding="utf-8")
    return f"Wrote {path}"


def graph_from_session(session: AcpSessionContext) -> CompiledAgentGraph:
    if not _deepagents_available():
        raise RuntimeError(
            'Install the optional DeepAgents dependency first: uv add "langchain-acp[deepagents]"'
        )
    deepagents = import_module("deepagents")
    create_deep_agent = deepagents.create_deep_agent
    return create_deep_agent(
        model=GenericFakeChatModel(
            messages=cycle([AIMessage(content="DeepAgents compatibility graph ready.")])
        ),
        tools=[list_workspace_files, read_file, write_file],
        interrupt_on={"write_file": True},
        name=f"deepagents-{session.cwd.name}",
    )


def _seed_session() -> AcpSessionContext:
    root = _ensure_workspace().resolve()
    timestamp = utc_now()
    return AcpSessionContext(
        session_id="deepagents-example",
        cwd=root,
        created_at=timestamp,
        updated_at=timestamp,
    )


graph = graph_from_session(_seed_session()) if _deepagents_available() else None

config = AdapterConfig(
    capability_bridges=[DeepAgentsCompatibilityBridge()],
    default_plan_generation_type="tools",
    projection_maps=[DeepAgentsProjectionMap()],
)


def main() -> None:
    _ensure_workspace()
    run_acp(graph_factory=graph_from_session, config=config)


if __name__ == "__main__":
    main()
