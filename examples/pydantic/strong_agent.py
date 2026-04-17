from __future__ import annotations as _annotations

import asyncio
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from acp import run_agent
from acp.interfaces import Agent as AcpAgent
from acp.schema import PlanEntry
from pydantic_acp import (
    AcpSessionContext,
    AdapterConfig,
    AgentBridgeBuilder,
    CapabilityBridge,
    ClientHostContext,
    FileSessionStore,
    FileSystemProjectionMap,
    HistoryProcessorBridge,
    NativeApprovalBridge,
    PrepareToolsBridge,
    PrepareToolsMode,
    RuntimeAgent,
    ThinkingBridge,
    create_acp_agent,
    truncate_text,
)
from pydantic_ai import Agent, ModelMessage
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import DeferredToolRequests, RunContext, ToolDefinition

__all__ = ("build_server_agent", "main")

_SEARCH_REPO_TOOL: Final[str] = "search_repo_paths"
_READ_REPO_TOOL: Final[str] = "read_repo_file"
_READ_WORKSPACE_TOOL: Final[str] = "read_workspace_note"
_WRITE_WORKSPACE_TOOL: Final[str] = "write_workspace_note"
_RUN_COMMAND_TOOL: Final[str] = "run_workspace_command"
_PLAN_STORAGE_DIR: Final[Path] = Path(".acpkit") / "plans"
_SESSION_STORE_DIR: Final[Path] = Path(".acp-sessions")
_SKIP_DIR_NAMES: Final[frozenset[str]] = frozenset(
    {
        ".acpkit",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "node_modules",
    }
)
_MUTATING_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {
        _WRITE_WORKSPACE_TOOL,
        _RUN_COMMAND_TOOL,
    }
)
_READ_PREVIEW_CHARS: Final[int] = 4000
_COMMAND_PREVIEW_CHARS: Final[int] = 8192


def _workspace_model() -> str | TestModel:
    configured_model = os.getenv("ACP_WORKSPACE_MODEL", "").strip()
    if configured_model:
        return configured_model
    return TestModel(
        call_tools=["describe_workspace_surface"],
        custom_output_text="Workspace example ready.",
    )


def _recent_history(messages: list[ModelMessage]) -> list[ModelMessage]:
    return list(messages[-4:])


def _filter_tools(
    tool_defs: list[ToolDefinition],
    *,
    blocked_names: frozenset[str],
) -> list[ToolDefinition]:
    return [tool_def for tool_def in tool_defs if tool_def.name not in blocked_names]


def _read_only_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return _filter_tools(tool_defs, blocked_names=_MUTATING_TOOL_NAMES)


def _all_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return list(tool_defs)


def _iter_repo_files(repo_root: Path) -> list[Path]:
    paths: list[Path] = []
    for root, dir_names, file_names in os.walk(repo_root):
        dir_names[:] = [name for name in dir_names if name not in _SKIP_DIR_NAMES]
        root_path = Path(root)
        for file_name in file_names:
            paths.append(root_path / file_name)
    return paths


def _search_repo_paths(repo_root: Path, query: str) -> str:
    normalized_query = query.strip().lower()
    if normalized_query == "":
        top_level_paths = sorted(
            path.relative_to(repo_root).as_posix()
            for path in repo_root.iterdir()
            if path.name not in _SKIP_DIR_NAMES
        )
        return "\n".join(("Query was empty. Top-level paths:", *top_level_paths[:20]))

    matches: list[str] = []
    for path in _iter_repo_files(repo_root):
        relative_path = path.relative_to(repo_root).as_posix()
        if normalized_query in relative_path.lower():
            matches.append(relative_path)
        if len(matches) >= 20:
            break
    if not matches:
        return f"No repository paths matched `{query}`."
    return "\n".join(matches)


def _resolve_repo_file(repo_root: Path, path: str) -> Path:
    candidate = (repo_root / path).resolve()
    try:
        relative_path = candidate.relative_to(repo_root)
    except ValueError as exc:
        raise ModelRetry("Path must stay inside the workspace root.") from exc
    if relative_path.parts and relative_path.parts[0] == ".acpkit":
        raise ModelRetry("ACP state files are not exposed through repository read tools.")
    if not candidate.is_file():
        raise ModelRetry(f"File not found: {path}")
    return candidate


def _read_repo_file(repo_root: Path, path: str, *, max_chars: int) -> str:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive.")
    file_path = _resolve_repo_file(repo_root, path)
    return truncate_text(
        file_path.read_text(encoding="utf-8"),
        limit=max_chars,
    )


def _plan_path(session: AcpSessionContext) -> Path:
    return session.cwd / _PLAN_STORAGE_DIR / f"{session.session_id}.md"


def _render_plan_snapshot(
    *,
    entries: Sequence[PlanEntry],
    plan_markdown: str | None,
) -> str:
    if not entries:
        return plan_markdown or "No ACP plan has been recorded yet."
    numbered_entries = "\n".join(
        f"{index}. [{entry.status}] ({entry.priority}) {entry.content}"
        for index, entry in enumerate(entries, start=1)
    )
    if not plan_markdown:
        return "\n\n".join(("Current ACP plan entries:", numbered_entries))
    return "\n\n".join((plan_markdown.rstrip(), "Current ACP plan entries:", numbered_entries))


def _build_capability_bridges() -> list[CapabilityBridge]:
    return [
        HistoryProcessorBridge(),
        ThinkingBridge(),
        PrepareToolsBridge(
            default_mode_id="ask",
            default_plan_generation_type="structured",
            modes=[
                PrepareToolsMode(
                    id="ask",
                    name="Ask",
                    description="Read-only repository and workspace inspection.",
                    prepare_func=_read_only_tools,
                ),
                PrepareToolsMode(
                    id="plan",
                    name="Plan",
                    description="Inspect the workspace and return a structured ACP plan.",
                    prepare_func=_read_only_tools,
                    plan_mode=True,
                ),
                PrepareToolsMode(
                    id="agent",
                    name="Agent",
                    description="Allow host-backed writes and command execution.",
                    prepare_func=_all_tools,
                    plan_tools=True,
                ),
            ],
        ),
    ]


def _build_projection_maps() -> list[FileSystemProjectionMap]:
    return [
        FileSystemProjectionMap(
            read_tool_names=frozenset({_READ_REPO_TOOL, _READ_WORKSPACE_TOOL}),
            write_tool_names=frozenset({_WRITE_WORKSPACE_TOOL}),
            bash_tool_names=frozenset({_RUN_COMMAND_TOOL}),
        )
    ]


@dataclass(slots=True, frozen=True, kw_only=True)
class WorkspacePlanPersistenceProvider:
    def persist_plan_state(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
        entries: Sequence[PlanEntry],
        plan_markdown: str | None,
    ) -> None:
        del agent
        storage_path = _plan_path(session)
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        storage_path.write_text(
            _render_plan_snapshot(entries=entries, plan_markdown=plan_markdown),
            encoding="utf-8",
        )


@dataclass(slots=True, kw_only=True)
class WorkspaceAgentSource:
    capability_bridges: list[CapabilityBridge]

    async def get_agent(
        self,
        session: AcpSessionContext,
    ) -> Agent[None, str | DeferredToolRequests]:
        repo_root = session.cwd.resolve()
        builder = AgentBridgeBuilder(
            session=session,
            capability_bridges=self.capability_bridges,
        )
        contributions = builder.build(plain_history_processors=[_recent_history])
        host_context = (
            ClientHostContext.from_bound_session(session) if session.client is not None else None
        )
        agent = Agent(
            _workspace_model(),
            name="workspace-example",
            output_type=[str, DeferredToolRequests],
            capabilities=contributions.capabilities,
            history_processors=contributions.history_processors,
            instructions=(
                "You are the ACP Kit workspace example. "
                "Use repository tools for inspection. "
                "When plan mode is active, return a real ACP plan instead of writing your own checklist. "
                "When write or command tools are visible, use them sparingly and let ACP approvals govern mutations."
            ),
        )

        @agent.tool_plain
        def describe_workspace_surface() -> str:
            """Summarize the ACP-facing workspace features available in this example."""

            return "\n".join(
                (
                    "Workspace example features:",
                    "- ask/plan/agent tool modes via PrepareToolsBridge",
                    "- structured native plan generation in plan mode",
                    "- plan progress tools in agent mode",
                    "- remembered native approvals for mutating tools",
                    "- file-backed ACP session and plan persistence",
                    f"- host context bound: {host_context is not None}",
                )
            )

        @agent.tool_plain(name=_SEARCH_REPO_TOOL)
        def search_repo_paths(query: str) -> str:
            """Search repository-relative paths by substring."""

            return _search_repo_paths(repo_root, query)

        @agent.tool_plain(name=_READ_REPO_TOOL)
        def read_repo_file(path: str, max_chars: int = _READ_PREVIEW_CHARS) -> str:
            """Read a repository file relative to the current workspace root."""

            return _read_repo_file(repo_root, path, max_chars=max_chars)

        if host_context is not None:

            @agent.tool(name=_READ_WORKSPACE_TOOL)
            async def read_workspace_note(ctx: RunContext[None], path: str) -> str:
                """Read a workspace file through the connected ACP host."""

                del ctx
                response = await host_context.filesystem.read_text_file(path)
                return response.content

            @agent.tool(name=_WRITE_WORKSPACE_TOOL, requires_approval=True)
            async def write_workspace_note(
                ctx: RunContext[None],
                path: str,
                content: str,
            ) -> str:
                """Write a workspace file through the connected ACP host."""

                del ctx
                response = await host_context.filesystem.write_text_file(path, content)
                if response is None:
                    return f"No write response returned for `{path}`."
                return f"Wrote `{path}`."

            @agent.tool(name=_RUN_COMMAND_TOOL, requires_approval=True)
            async def run_workspace_command(
                ctx: RunContext[None],
                command: str,
            ) -> dict[str, str | int]:
                """Run a shell command through the connected ACP host terminal."""

                del ctx
                terminal = await host_context.terminal.create_terminal(
                    "bash",
                    args=["-lc", command],
                    cwd=str(session.cwd),
                    output_byte_limit=_COMMAND_PREVIEW_CHARS,
                )
                result = await host_context.terminal.wait_for_terminal_exit(terminal.terminal_id)
                output = await host_context.terminal.terminal_output(terminal.terminal_id)
                await host_context.terminal.release_terminal(terminal.terminal_id)
                stderr = ""
                if result.signal is not None:
                    stderr = f"Process terminated by signal `{result.signal}`."
                return {
                    "command": command,
                    "returncode": result.exit_code if result.exit_code is not None else 1,
                    "timed_out": 0,
                    "stdout": truncate_text(output.output, limit=_COMMAND_PREVIEW_CHARS),
                    "stderr": stderr,
                }

        return agent

    async def get_deps(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> None:
        del session, agent
        return None


def build_server_agent() -> AcpAgent:
    capability_bridges = _build_capability_bridges()
    session_store_dir = Path.cwd() / _SESSION_STORE_DIR
    session_store_dir.mkdir(parents=True, exist_ok=True)
    return create_acp_agent(
        agent_source=WorkspaceAgentSource(capability_bridges=capability_bridges),
        config=AdapterConfig(
            agent_name="workspace-example",
            agent_title="ACP Kit Workspace Example",
            approval_bridge=NativeApprovalBridge(enable_persistent_choices=True),
            capability_bridges=list(capability_bridges),
            native_plan_additional_instructions=(
                "Keep plans short. Use in-progress only when work spans multiple actions or turns."
            ),
            native_plan_persistence_provider=WorkspacePlanPersistenceProvider(),
            projection_maps=_build_projection_maps(),
            session_store=FileSessionStore(session_store_dir),
        ),
    )


def main() -> None:
    asyncio.run(run_agent(build_server_agent()))


if __name__ == "__main__":
    main()
