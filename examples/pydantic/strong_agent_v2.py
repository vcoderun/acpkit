from __future__ import annotations as _annotations

import asyncio
import os
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias

from acp import run_agent
from acp.interfaces import Agent as AcpAgent
from acp.schema import PlanEntry, SessionMode
from dotenv import load_dotenv
from pydantic_acp import (
    AcpSessionContext,
    AdapterConfig,
    AgentBridgeBuilder,
    AgentBridgeContributions,
    CapabilityBridge,
    ClientHostContext,
    FileSessionStore,
    FileSystemProjectionMap,
    HistoryProcessorBridge,
    HookBridge,
    McpBridge,
    McpServerDefinition,
    McpToolDefinition,
    ModeState,
    NativeApprovalBridge,
    PrepareToolsBridge,
    PrepareToolsMode,
    ThinkingBridge,
    create_acp_agent,
)
from pydantic_ai import Agent, ModelMessage
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.tools import DeferredToolRequests, RunContext, ToolDefinition

load_dotenv()

__all__ = ("build_server_agent", "main")

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

_APPROVAL_POLICIES_KEY: Final = "approval_policies"
_DEFAULT_MODEL: Final[str] = "openrouter:google/gemini-3-flash-preview"
_TERMINAL_OUTPUT_LIMIT: Final[int] = 8192
_PLAN_STORAGE_DIR: Final[Path] = Path(".acpkit") / "plans"
_SEARCH_REPO_TOOL: Final[str] = "mcp_repo_search_paths"
_READ_REPO_TOOL: Final[str] = "mcp_repo_read_file"
_READ_WORKSPACE_TOOL: Final[str] = "mcp_host_read_workspace_file"
_WRITE_WORKSPACE_TOOL: Final[str] = "mcp_host_write_workspace_file"
_RUN_COMMAND_TOOL: Final[str] = "mcp_host_run_command"
_READ_PLANS_TOOL: Final[str] = "read_plans"
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
        "references",
    }
)
_ASK_BLOCKED_TOOLS: Final[frozenset[str]] = frozenset(
    {
        _READ_WORKSPACE_TOOL,
        _WRITE_WORKSPACE_TOOL,
        _RUN_COMMAND_TOOL,
        _READ_PLANS_TOOL,
    }
)
_PLAN_BLOCKED_TOOLS: Final[frozenset[str]] = frozenset(
    {
        _READ_PLANS_TOOL,
        _READ_WORKSPACE_TOOL,
        _RUN_COMMAND_TOOL,
        _WRITE_WORKSPACE_TOOL,
    }
)
_AGENT_BLOCKED_TOOLS: Final[frozenset[str]] = frozenset()
_WORKSPACE_MODES: Final[tuple[SessionMode, ...]] = (
    SessionMode(
        id="ask",
        name="Ask",
        description="Read-only repository inspection without host-side tools.",
    ),
    SessionMode(
        id="plan",
        name="Plan",
        description="Inspect the repo and draft the ACP plan state before acting.",
    ),
    SessionMode(
        id="agent",
        name="Agent",
        description="Expose the full workspace tool surface, including writes.",
    ),
)


def _model_name() -> str:
    return os.getenv("MODEL_NAME", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL


def _iter_repo_paths(repo_root: Path) -> list[Path]:
    paths: list[Path] = []
    for root, dir_names, file_names in os.walk(repo_root):
        dir_names[:] = [name for name in dir_names if name not in _SKIP_DIR_NAMES]
        root_path = Path(root)
        for file_name in file_names:
            paths.append(root_path / file_name)
    return paths


def _resolve_repo_path(repo_root: Path, path: str) -> Path:
    candidate = (repo_root / path).resolve()
    try:
        relative_path = candidate.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError("Path must stay inside the repository root.") from exc
    if relative_path.parts and relative_path.parts[0] == ".acpkit":
        raise ValueError("ACP internal storage is not readable through repository tools.")
    if not candidate.is_file():
        raise ValueError(f"File not found: {path}")
    return candidate


def _current_plan_storage_path(session: AcpSessionContext) -> Path:
    return session.cwd / _PLAN_STORAGE_DIR / f"{session.session_id}.md"


def _render_plan_document(
    *,
    entries: Sequence[PlanEntry],
    plan_markdown: str | None,
) -> str:
    if not entries:
        return plan_markdown or "No plan has been recorded yet."
    numbered_entries = "\n".join(
        f"{index}. [{entry.status}] ({entry.priority}) {entry.content}"
        for index, entry in enumerate(entries, start=1)
    )
    if not plan_markdown:
        return "\n\n".join(("Current plan entries:", numbered_entries))
    return "\n\n".join(
        (
            plan_markdown.rstrip(),
            "Current plan entries:",
            numbered_entries,
        )
    )


def _truncate_text(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n\n...[truncated]"


def _tool_retry(message: str) -> ModelRetry:
    return ModelRetry(message)


def _trim_history(messages: list[ModelMessage]) -> list[ModelMessage]:
    return list(messages[-4:])


def _contextual_history(
    ctx: RunContext[None],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    keep_count = 4 if ctx.run_step <= 1 else 6
    return list(messages[-keep_count:])


def _filter_tools(
    tool_defs: list[ToolDefinition],
    *,
    blocked_names: frozenset[str],
) -> list[ToolDefinition]:
    return [tool_def for tool_def in tool_defs if tool_def.name not in blocked_names]


def _agent_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return _filter_tools(tool_defs, blocked_names=_AGENT_BLOCKED_TOOLS)


def _plan_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return _filter_tools(tool_defs, blocked_names=_PLAN_BLOCKED_TOOLS)


def _ask_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return _filter_tools(tool_defs, blocked_names=_ASK_BLOCKED_TOOLS)


def _build_bridges() -> list[CapabilityBridge]:
    bridges: list[CapabilityBridge] = [
        HookBridge(hide_all=True),
        HistoryProcessorBridge(),
        ThinkingBridge(),
        PrepareToolsBridge(
            default_mode_id="ask",
            modes=[
                PrepareToolsMode(
                    id="ask",
                    name="Ask",
                    description="Read-only repository inspection without host-side tools.",
                    prepare_func=_ask_tools,
                ),
                PrepareToolsMode(
                    id="plan",
                    name="Plan",
                    plan_mode=True,
                    description="Inspect the repo and draft the ACP plan.",
                    prepare_func=_plan_tools,
                ),
                PrepareToolsMode(
                    id="agent",
                    name="Agent",
                    description="Expose the full workspace tool surface, including writes.",
                    prepare_func=_agent_tools,
                    plan_tools=True,
                ),
            ],
        ),
        McpBridge(
            approval_policy_scope="prefix",
            servers=[
                McpServerDefinition(
                    server_id="repo",
                    name="Repository",
                    transport="http",
                    tool_prefix="mcp_repo_",
                    description="Repository inspection tools.",
                ),
                McpServerDefinition(
                    server_id="host",
                    name="Host Environment",
                    transport="http",
                    tool_prefix="mcp.host.",
                    description="Client-backed filesystem and terminal tools.",
                ),
            ],
            tools=[
                McpToolDefinition(tool_name=_SEARCH_REPO_TOOL, server_id="repo", kind="search"),
                McpToolDefinition(tool_name=_READ_REPO_TOOL, server_id="repo", kind="read"),
                McpToolDefinition(
                    tool_name=_READ_WORKSPACE_TOOL,
                    server_id="host",
                    kind="read",
                ),
                McpToolDefinition(
                    tool_name=_WRITE_WORKSPACE_TOOL,
                    server_id="host",
                    kind="edit",
                ),
                McpToolDefinition(
                    tool_name=_RUN_COMMAND_TOOL,
                    server_id="host",
                    kind="execute",
                ),
            ],
        ),
    ]
    return bridges


def _build_projection_maps() -> tuple[FileSystemProjectionMap, ...]:
    return (
        FileSystemProjectionMap(
            read_tool_names=frozenset({_READ_REPO_TOOL, _READ_WORKSPACE_TOOL}),
            write_tool_names=frozenset({_WRITE_WORKSPACE_TOOL}),
            bash_tool_names=frozenset({_RUN_COMMAND_TOOL}),
        ),
    )


@dataclass(slots=True, frozen=True, kw_only=True)
class WorkspaceModesProvider:
    def get_mode_state(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str | DeferredToolRequests],
    ) -> ModeState:
        del agent
        current_mode_id = str(session.config_values.get("mode", "ask"))
        return ModeState(
            modes=list(_WORKSPACE_MODES),
            current_mode_id=current_mode_id,
        )

    def set_mode(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str | DeferredToolRequests],
        mode_id: str,
    ) -> ModeState:
        session.config_values["mode"] = mode_id
        session.message_history_json = None
        return self.get_mode_state(session, agent)


@dataclass(slots=True, frozen=True, kw_only=True)
class WorkspaceApprovalStateProvider:
    def get_approval_state(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str | DeferredToolRequests],
    ) -> dict[str, JsonValue]:
        del agent
        remembered = session.metadata.get(_APPROVAL_POLICIES_KEY)
        remembered_policy_count = len(remembered) if isinstance(remembered, dict) else 0
        return {
            "host_context_bound": session.client is not None,
            "mode": str(session.config_values.get("mode", "ask")),
            "remembered_policy_count": remembered_policy_count,
        }


@dataclass(slots=True, frozen=True, kw_only=True)
class WorkspaceNativePlanPersistenceProvider:
    def persist_plan_state(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str | DeferredToolRequests],
        entries: Sequence[PlanEntry],
        plan_markdown: str | None,
    ) -> None:
        del agent
        storage_path = _current_plan_storage_path(session)
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        storage_path.write_text(
            _render_plan_document(entries=entries, plan_markdown=plan_markdown),
            encoding="utf-8",
        )


@dataclass(slots=True, kw_only=True)
class WorkspaceAgentSource:
    capability_bridges: list[CapabilityBridge]

    async def get_agent(
        self, session: AcpSessionContext
    ) -> Agent[None, str | DeferredToolRequests]:
        repo_root = session.cwd.resolve()
        builder: AgentBridgeBuilder[None] = AgentBridgeBuilder(
            session=session,
            capability_bridges=self.capability_bridges,
        )
        contributions: AgentBridgeContributions[None] = builder.build(
            contextual_history_processors=[_contextual_history],
            plain_history_processors=[_trim_history],
        )
        host_context = (
            ClientHostContext.from_bound_session(session) if session.client is not None else None
        )

        agent: Agent[None, str | DeferredToolRequests] = Agent(
            _model_name(),
            name="acpkit_workspace_agent",
            output_type=[str, DeferredToolRequests],
            capabilities=contributions.capabilities,
            history_processors=contributions.history_processors,
            instructions=(
                "You are the ACP Kit workspace agent. "
                "Use tools when they materially help. "
                "The host may change your available tools and operating constraints between turns. "
                "Do not claim to know hidden host mode names or internal tool groups. "
                "If the user asks about internal mode state, explain that the host manages it and you can only rely on the tools available in the current turn. "
                "When plan tools are available and you create or revise a plan, record it with `acp_set_plan`. "
                "ACP persists the current session plan automatically, so do not manage `.acpkit` paths yourself. "
                "When the user asks you to start the current plan, continue it, or implement a specific plan item, first read the current plan with `acp_get_plan` when that tool is available. "
                "When plan progress tools are available, use the same 1-based entry number shown there with `acp_update_plan_entry`, do only the requested step, then mark it completed with `acp_mark_plan_done`. "
                "Do not mark multiple plan items completed unless you actually finished them. "
                "Mutating tools may require approval; let the host flow decide."
            ),
        )

        @agent.tool_plain
        def describe_workspace_surface() -> str:
            """Return the ACP-facing surfaces available in this workspace agent."""

            return "\n".join(
                (
                    "Workspace agent surfaces:",
                    "- session-aware factory via AgentSource",
                    "- file-backed session persistence",
                    "- session-local host-managed tool-state provider",
                    "- native deferred approvals with remembered choices",
                    "- history and MCP bridge wiring",
                    "- filesystem-aware diff projection for repo and host file tools",
                    "- native ACP plans persisted automatically per session",
                    f"- model: {_model_name()}",
                    f"- host context bound: {host_context is not None}",
                )
            )

        @agent.tool_plain(name=_SEARCH_REPO_TOOL)
        def search_repo_paths(query: str) -> str:
            """Search repository-relative file paths by substring."""

            normalized_query = query.strip().lower()
            if not normalized_query:
                top_level_paths = sorted(
                    path.relative_to(repo_root).as_posix()
                    for path in repo_root.iterdir()
                    if path.name not in _SKIP_DIR_NAMES
                )
                return "\n".join(("Query was empty. Top-level repo paths:", *top_level_paths[:20]))

            matches: list[str] = []
            for path in _iter_repo_paths(repo_root):
                relative_path = path.relative_to(repo_root).as_posix()
                if normalized_query in relative_path.lower():
                    matches.append(relative_path)
                if len(matches) >= 20:
                    break
            if not matches:
                return f"No repo paths matched `{query}`."
            return "\n".join(matches)

        @agent.tool_plain(name=_READ_REPO_TOOL)
        def read_repo_file(path: str, max_chars: int = 4000) -> str:
            """Read a repository file relative to the repo root."""

            if max_chars <= 0:
                raise ValueError("max_chars must be positive.")
            try:
                file_path = _resolve_repo_path(repo_root, path)
            except ValueError as exc:
                raise _tool_retry(str(exc)) from exc
            text = file_path.read_text(encoding="utf-8")
            return _truncate_text(text, limit=max_chars)

        @agent.tool_plain(name=_READ_PLANS_TOOL)
        def read_plans(max_chars: int = 4000) -> str:
            """Read the current persisted ACP plan file for this session."""

            if max_chars <= 0:
                raise ValueError("max_chars must be positive.")
            storage_path = _current_plan_storage_path(session)
            if not storage_path.is_file():
                return _truncate_text(
                    _render_plan_document(
                        entries=[PlanEntry.model_validate(entry) for entry in session.plan_entries],
                        plan_markdown=session.plan_markdown,
                    ),
                    limit=max_chars,
                )
            return _truncate_text(storage_path.read_text(encoding="utf-8"), limit=max_chars)

        if host_context is not None:

            @agent.tool(name=_READ_WORKSPACE_TOOL)
            async def read_workspace_file(ctx: RunContext[None], path: str) -> str:
                """Read a file through the ACP client-backed filesystem backend."""

                del ctx
                try:
                    response = await host_context.filesystem.read_text_file(path)
                except (FileNotFoundError, PermissionError) as exc:
                    raise _tool_retry(str(exc)) from exc
                return response.content

            async def _ensure_dir(dir_path: str) -> None:
                """Create a directory and its parents via the terminal backend."""
                t = await host_context.terminal.create_terminal(
                    "bash",
                    args=["-lc", f"mkdir -p {shlex.quote(dir_path)}"],
                    cwd=str(session.cwd),
                )
                await host_context.terminal.wait_for_terminal_exit(t.terminal_id)
                await host_context.terminal.release_terminal(t.terminal_id)

            @agent.tool(name=_WRITE_WORKSPACE_TOOL, requires_approval=True)
            async def write_workspace_file(
                ctx: RunContext[None],
                path: str,
                content: str,
            ) -> str:
                """Write a file through the ACP client-backed filesystem backend."""

                del ctx
                parent = Path(path).parent
                try:
                    if str(parent) not in ("", "."):
                        await _ensure_dir(str(parent))
                    response = await host_context.filesystem.write_text_file(path, content)
                except (FileNotFoundError, PermissionError) as exc:
                    raise _tool_retry(str(exc)) from exc
                if response is None:
                    return f"No write response returned for `{path}`."
                return f"Wrote workspace file `{path}`."

            @agent.tool(name=_RUN_COMMAND_TOOL, requires_approval=True)
            async def run_command(
                ctx: RunContext[None],
                command: str,
            ) -> dict[str, str | int]:
                """Run a shell command through the ACP client-backed terminal backend."""

                del ctx
                terminal = await host_context.terminal.create_terminal(
                    "bash",
                    args=["-lc", command],
                    cwd=str(session.cwd),
                    output_byte_limit=_TERMINAL_OUTPUT_LIMIT,
                )
                result = await host_context.terminal.wait_for_terminal_exit(terminal.terminal_id)
                output = await host_context.terminal.terminal_output(terminal.terminal_id)
                await host_context.terminal.release_terminal(terminal.terminal_id)
                return {
                    "command": command,
                    "returncode": (result.exit_code if result.exit_code is not None else 1),
                    "timed_out": 0,
                    "stdout": _truncate_text(output.output, limit=_TERMINAL_OUTPUT_LIMIT),
                    "stderr": (
                        ""
                        if result.signal is None
                        else f"Process terminated by signal `{result.signal}`."
                    ),
                }

        return agent

    async def get_deps(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str | DeferredToolRequests],
    ) -> None:
        del session, agent
        return None


def build_server_agent() -> AcpAgent:
    session_store_dir = Path.cwd() / ".acp-sessions"
    session_store_dir.mkdir(parents=True, exist_ok=True)
    capability_bridges = _build_bridges()
    return create_acp_agent(
        agent_source=WorkspaceAgentSource(
            capability_bridges=capability_bridges,
        ),
        config=AdapterConfig(
            agent_name="acpkit_workspace_agent",
            agent_title="ACP Kit Workspace Agent",
            approval_bridge=NativeApprovalBridge(enable_persistent_choices=True),
            approval_state_provider=WorkspaceApprovalStateProvider(),
            capability_bridges=list(capability_bridges),
            modes_provider=WorkspaceModesProvider(),
            native_plan_persistence_provider=WorkspaceNativePlanPersistenceProvider(),
            projection_maps=_build_projection_maps(),
            session_store=FileSessionStore(session_store_dir),
        ),
    )


def main() -> None:
    asyncio.run(run_agent(build_server_agent()))


if __name__ == "__main__":
    main()
