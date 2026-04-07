from __future__ import annotations as _annotations

import asyncio
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias

from acp import run_agent
from acp.interfaces import Agent as AcpAgent
from acp.schema import SessionMode
from dotenv import load_dotenv
from pydantic_acp import (
    AcpSessionContext,
    AdapterConfig,
    AgentBridgeBuilder,
    AgentBridgeContributions,
    ClientHostContext,
    FileSessionStore,
    FileSystemProjectionMap,
    HistoryProcessorBridge,
    HookBridge,
    HookProjectionMap,
    McpBridge,
    McpServerDefinition,
    McpToolDefinition,
    ModeState,
    NativeApprovalBridge,
    PrepareToolsBridge,
    PrepareToolsMode,
    create_acp_agent,
)
from pydantic_ai import Agent, ModelMessage
from pydantic_ai.tools import DeferredToolRequests, RunContext, ToolDefinition

load_dotenv()

__all__ = ("build_server_agent", "main")

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

_APPROVAL_POLICIES_KEY: Final = "approval_policies"
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

_DEFAULT_MODEL: Final[str] = "openai:gpt-4o"
_TERMINAL_OUTPUT_LIMIT: Final[int] = 8192
_PLAN_WRITABLE_DIR: Final[str] = "acpkit/plans"
_SEARCH_REPO_TOOL: Final[str] = "mcp_repo_search_paths"
_READ_REPO_TOOL: Final[str] = "mcp_repo_read_file"
_READ_WORKSPACE_TOOL: Final[str] = "mcp_host_read_workspace_file"
_WRITE_WORKSPACE_TOOL: Final[str] = "mcp_host_write_workspace_file"
_RUN_COMMAND_TOOL: Final[str] = "mcp_host_run_command"
_SKIP_DIR_NAMES: Final[frozenset[str]] = frozenset(
    {
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


def _model_name() -> str:
    return os.getenv("MODEL_NAME", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL


def _iter_repo_paths() -> list[Path]:
    paths: list[Path] = []
    for root, dir_names, file_names in os.walk(_REPO_ROOT):
        dir_names[:] = [name for name in dir_names if name not in _SKIP_DIR_NAMES]
        root_path = Path(root)
        for file_name in file_names:
            paths.append(root_path / file_name)
    return paths


def _resolve_repo_path(path: str) -> Path:
    candidate = (_REPO_ROOT / path).resolve()
    try:
        candidate.relative_to(_REPO_ROOT)
    except ValueError as exc:
        raise ValueError("Path must stay inside the repository root.") from exc
    if not candidate.is_file():
        raise ValueError(f"File not found: {path}")
    return candidate


def _is_plan_mode_write_allowed(path: str) -> bool:
    normalized_path = path.strip().replace("\\", "/").lstrip("./")
    if not normalized_path:
        return False
    return normalized_path == _PLAN_WRITABLE_DIR or normalized_path.startswith(
        f"{_PLAN_WRITABLE_DIR}/"
    )


def _truncate_text(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n\n...[truncated]"


def _trim_history(messages: list[ModelMessage]) -> list[ModelMessage]:
    return list(messages[-4:])


def _contextual_history(
    ctx: RunContext[None],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    keep_count = 4 if ctx.run_step <= 1 else 6
    return list(messages[-keep_count:])


def _agent_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return list(tool_defs)


def _plan_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return list(tool_defs)


def _ask_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    blocked_names = {
        _READ_WORKSPACE_TOOL,
        _WRITE_WORKSPACE_TOOL,
        _RUN_COMMAND_TOOL,
    }
    return [tool_def for tool_def in tool_defs if tool_def.name not in blocked_names]


def _build_bridges() -> list[
    HookBridge | HistoryProcessorBridge | PrepareToolsBridge[None] | McpBridge
]:
    return [
        HookBridge(
            record_event_stream=False,
            record_node_lifecycle=False,
            record_prepare_tools=False,
            record_run_lifecycle=False,
            record_tool_validation=False,
        ),
        HistoryProcessorBridge(),
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
                    description="Inspect the repo, run host checks, and write plans under .acpkit/plans.",
                    prepare_func=_plan_tools,
                ),
                PrepareToolsMode(
                    id="agent",
                    name="Agent",
                    description="Expose the full workspace tool surface, including writes.",
                    prepare_func=_agent_tools,
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
            modes=[
                SessionMode(
                    id="ask",
                    name="Ask",
                    description="Read-only repository inspection without host-side tools.",
                ),
                SessionMode(
                    id="plan",
                    name="Plan",
                    description="Inspect the repo and run non-writing host checks before acting.",
                ),
                SessionMode(
                    id="agent",
                    name="Agent",
                    description="Expose the full workspace tool surface, including writes.",
                ),
            ],
            current_mode_id=current_mode_id,
        )

    def set_mode(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str | DeferredToolRequests],
        mode_id: str,
    ) -> ModeState:
        session.config_values["mode"] = mode_id
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


@dataclass(slots=True, kw_only=True)
class WorkspaceAgentSource:
    capability_bridges: list[
        HookBridge | HistoryProcessorBridge | PrepareToolsBridge[None] | McpBridge
    ]

    async def get_agent(
        self, session: AcpSessionContext
    ) -> Agent[None, str | DeferredToolRequests]:
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

        agent: Agent[None, str | DeferredToolRequests] = Agent(  # pyright: ignore[reportCallIssue]
            _model_name(),
            name="acpkit_workspace_agent",
            output_type=[str, DeferredToolRequests],
            capabilities=contributions.capabilities,
            history_processors=contributions.history_processors,
            instructions=(
                "You are the ACP Kit workspace agent. "
                "Use tools when they materially help. Respect the active mode. "
                "`ask` mode is read-only and repository-focused. "
                "`plan` mode can inspect the repository, run host-backed commands, and only write under .acpkit/plans. "
                "`agent` mode exposes the full workspace tool surface. "
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
                    "- session-local modes owned by provider",
                    "- native deferred approvals with remembered choices",
                    "- hook, history, prepare-tools, and MCP bridges",
                    "- filesystem-aware diff projection for repo and host file tools",
                    "- bash preview rendering for host-backed command execution",
                    "- modes: ask, plan, agent",
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
                    path.relative_to(_REPO_ROOT).as_posix() for path in _REPO_ROOT.iterdir()
                )
                return "\n".join(("Query was empty. Top-level repo paths:", *top_level_paths[:20]))

            matches: list[str] = []
            for path in _iter_repo_paths():
                relative_path = path.relative_to(_REPO_ROOT).as_posix()
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
            file_path = _resolve_repo_path(path)
            text = file_path.read_text(encoding="utf-8")
            return _truncate_text(text, limit=max_chars)

        if host_context is not None:

            @agent.tool(name=_READ_WORKSPACE_TOOL)
            async def read_workspace_file(ctx: RunContext[None], path: str) -> str:
                """Read a file through the ACP client-backed filesystem backend."""

                del ctx
                response = await host_context.filesystem.read_text_file(path)
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
                if str(
                    session.config_values.get("mode", "ask")
                ) == "plan" and not _is_plan_mode_write_allowed(path):
                    raise ValueError("Plan mode may only write under `./.acpkit/plans`.")
                parent = Path(path).parent
                if str(parent) not in ("", "."):
                    await _ensure_dir(str(parent))
                response = await host_context.filesystem.write_text_file(path, content)
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
            hook_projection_map=HookProjectionMap(
                hidden_event_ids=frozenset(
                    {
                        "after_model_request",
                        "tool_execute",
                        "tool_execute_error",
                    }
                )
            ),
            modes_provider=WorkspaceModesProvider(),
            projection_maps=_build_projection_maps(),
            session_store=FileSessionStore(session_store_dir),
        ),
    )


def main() -> None:
    asyncio.run(run_agent(build_server_agent()))


if __name__ == "__main__":
    main()
