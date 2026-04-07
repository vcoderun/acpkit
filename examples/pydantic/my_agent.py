from __future__ import annotations as _annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, TypeAlias

from acp import run_agent
from acp.interfaces import Agent as AcpAgent
from acp.interfaces import Client as AcpClient
from acp.schema import (
    AudioContentBlock,
    AuthenticateResponse,
    ClientCapabilities,
    CloseSessionResponse,
    EmbeddedResourceContentBlock,
    ForkSessionResponse,
    HttpMcpServer,
    ImageContentBlock,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    McpServerStdio,
    NewSessionResponse,
    PlanEntry,
    PromptResponse,
    ResourceContentBlock,
    ResumeSessionResponse,
    SessionConfigOptionBoolean,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SessionMode,
    SetSessionConfigOptionResponse,
    SetSessionModelResponse,
    SetSessionModeResponse,
    SseMcpServer,
    TextContentBlock,
)
from pydantic_acp import (
    AcpSessionContext,
    AdapterConfig,
    AdapterModel,
    AgentBridgeBuilder,
    ClientHostContext,
    ConfigOption,
    FileSessionStore,
    FileSystemProjectionMap,
    HistoryProcessorBridge,
    HookBridge,
    McpBridge,
    McpServerDefinition,
    McpToolDefinition,
    ModelSelectionState,
    ModeState,
    NativeApprovalBridge,
    PrepareToolsBridge,
    PrepareToolsMode,
    create_acp_agent,
)
from pydantic_ai import (
    Agent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.messages import ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import DeferredToolRequests, RunContext, ToolDefinition

__all__ = ("build_server_agent", "main")

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
PromptBlock: TypeAlias = (
    TextContentBlock
    | ImageContentBlock
    | AudioContentBlock
    | ResourceContentBlock
    | EmbeddedResourceContentBlock
)

_APPROVAL_POLICIES_KEY: Final = "approval_policies"
_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_SESSION_STORE_DIR: Final[Path] = _REPO_ROOT / ".demo-sessions" / "structured-agent"
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
_DIRECT_MODEL: Final = TestModel(
    custom_output_text=(
        "Direct fixture model selected. Switch back to `router` or `live` to get tool-driven "
        "behavior."
    )
)
_DEMO_NOTES: dict[str, str] = {
    "adapter-status": (
        "Implemented milestones: lifecycle, model selection, approvals, factories, providers, "
        "bridges, and client-backed host helpers."
    ),
    "mode-reminder": (
        "Switch the ACP session mode to `review` to expose MCP-style repo and host tools."
    ),
    "workspace-scope": (
        "Use the `workspace_scope` config option to steer file reads toward repo paths or "
        "client-backed host paths."
    ),
}


def _env_model_name() -> str | None:
    configured_model = os.getenv("ACP_DEMO_MODEL", "").strip()
    return configured_model or None


def _build_router_model() -> FunctionModel:
    return FunctionModel(_route_demo_prompt, model_name="acpkit-structured-demo-router")


def _available_models() -> list[AdapterModel]:
    models = [
        AdapterModel(
            model_id="router",
            name="Router",
            description="Deterministic tool router for exercising ACP features.",
            override=_ROUTER_MODEL,
        ),
        AdapterModel(
            model_id="direct",
            name="Direct",
            description="Deterministic plain-text fixture model.",
            override=_DIRECT_MODEL,
        ),
    ]
    env_model_name = _env_model_name()
    if env_model_name is not None:
        models.append(
            AdapterModel(
                model_id="live",
                name="Live",
                description=f"Real model from ACP_DEMO_MODEL: {env_model_name}",
                override=env_model_name,
            )
        )
    return models


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


def _truncate_text(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n\n...[truncated]"


def _normalize_note_name(name: str) -> str:
    normalized = "-".join(name.strip().lower().split())
    if not normalized:
        raise ValueError("Note name cannot be empty.")
    return normalized


def _latest_user_prompt(messages: list[ModelMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            for part in reversed(message.parts):
                if isinstance(part, UserPromptPart):
                    return str(part.content)
    return ""


def _tool_result_response(messages: list[ModelMessage]) -> ModelResponse | None:
    if not messages or not isinstance(messages[-1], ModelRequest):
        return None

    tool_returns = [part for part in messages[-1].parts if isinstance(part, ToolReturnPart)]
    if not tool_returns:
        return None

    rendered_returns = [f"{part.tool_name}: {part.content}" for part in tool_returns]
    return ModelResponse(parts=[TextPart("\n".join(rendered_returns))])


def _tool_names(info: AgentInfo) -> set[str]:
    return {tool.name for tool in info.function_tools}


def _call_tool(tool_name: str, **kwargs: str | int) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name, kwargs)])


def _route_demo_prompt(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    tool_result_response = _tool_result_response(messages)
    if tool_result_response is not None:
        return tool_result_response

    prompt = _latest_user_prompt(messages).strip()
    lowered_prompt = prompt.lower()
    available_tools = _tool_names(info)

    if "capabilities" in lowered_prompt or "what can you do" in lowered_prompt:
        return _call_tool("fetch_supported_capabilities")
    if lowered_prompt in {
        "notları oku",
        "notlari oku",
        "notlari listele",
        "notları listele",
    }:
        return _call_tool("list_notes")
    if lowered_prompt.startswith("read note "):
        return _call_tool("read_note", name=prompt[10:].strip())
    if lowered_prompt.startswith("search notes "):
        return _call_tool("search_notes", query=prompt[13:].strip())
    if lowered_prompt.startswith("update note "):
        payload = prompt[12:].strip()
        if ":" in payload:
            name, content = payload.split(":", 1)
            return _call_tool("update_note", name=name.strip(), content=content.strip())
        return ModelResponse(
            parts=[TextPart("Use `update note <name>: <content>` for approval-gated note edits.")]
        )
    if lowered_prompt.startswith("delete note "):
        return _call_tool("delete_note", name=prompt[12:].strip())

    if lowered_prompt.startswith("search repo "):
        query = prompt[12:].strip()
        if "mcp.repo.search_paths" in available_tools:
            return _call_tool("mcp.repo.search_paths", query=query)
        return ModelResponse(
            parts=[
                TextPart(
                    "Repo tools are hidden in the current mode. Switch the session mode to `review`."
                )
            ]
        )

    if lowered_prompt.startswith("read repo file "):
        path = prompt[15:].strip()
        if "mcp.repo.read_file" in available_tools:
            return _call_tool("mcp.repo.read_file", path=path)
        return ModelResponse(
            parts=[
                TextPart(
                    "Repo file tools are hidden in the current mode. Switch the session mode to `review`."
                )
            ]
        )

    if lowered_prompt.startswith("read workspace file "):
        path = prompt[20:].strip()
        if "mcp.host.read_workspace_file" in available_tools:
            return _call_tool("mcp.host.read_workspace_file", path=path)
        if "mcp.repo.read_file" in available_tools:
            return _call_tool("mcp.repo.read_file", path=path)
        return ModelResponse(
            parts=[TextPart("Workspace file tools are unavailable. Switch to `review` mode first.")]
        )

    if "python version" in lowered_prompt or "python sürümü" in lowered_prompt:
        if "mcp.host.python_version" in available_tools:
            return _call_tool("mcp.host.python_version")
        return ModelResponse(
            parts=[TextPart("Host terminal tools are unavailable. Switch to `review` mode first.")]
        )

    if "kill demo process" in lowered_prompt and "mcp.host.kill_demo_process" in available_tools:
        return _call_tool("mcp.host.kill_demo_process")

    return ModelResponse(
        parts=[
            TextPart(
                "\n".join(
                    (
                        "Structured ACP demo is active.",
                        "Try one of these prompts:",
                        "- capabilities",
                        "- notları oku",
                        "- search notes mode",
                        "- update note scratch: hello world",
                        "- delete note scratch",
                        "- search repo spec",
                        "- read repo file README.md",
                        "- read workspace file README.md",
                        "- python version",
                        "",
                        "ACP client actions to try:",
                        "- switch session mode to `review`",
                        "- switch model to `direct` or `live`",
                        "- toggle `verbose_notes` and `mcp_auto_connect` config options",
                    )
                )
            )
        ]
    )


_ROUTER_MODEL: Final = _build_router_model()


def _trim_history(messages: list[ModelMessage]) -> list[ModelMessage]:
    return list(messages[-4:])


def _contextual_history(
    ctx: RunContext[None],
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    keep_count = 4 if ctx.run_step <= 1 else 6
    return list(messages[-keep_count:])


def _review_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return list(tool_defs)


def _chat_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return [tool_def for tool_def in tool_defs if not tool_def.name.startswith("mcp.")]


def _build_bridges() -> list[
    HookBridge | HistoryProcessorBridge | PrepareToolsBridge[None] | McpBridge
]:
    return [
        HookBridge(),
        HistoryProcessorBridge(),
        PrepareToolsBridge(
            default_mode_id="chat",
            modes=[
                PrepareToolsMode(
                    id="chat",
                    name="Chat",
                    description="Hide MCP-style tools for lightweight conversation.",
                    prepare_func=_chat_tools,
                ),
                PrepareToolsMode(
                    id="review",
                    name="Review",
                    description="Expose MCP-style repo and host tools.",
                    prepare_func=_review_tools,
                ),
            ],
        ),
        McpBridge(
            approval_policy_scope="prefix",
            config_options=[
                SessionConfigOptionBoolean(
                    id="mcp_auto_connect",
                    name="Auto Connect MCP",
                    category="mcp",
                    description="Remember MCP connection preference inside the session.",
                    type="boolean",
                    current_value=False,
                )
            ],
            servers=[
                McpServerDefinition(
                    server_id="repo",
                    name="Repo MCP",
                    transport="http",
                    tool_prefix="mcp.repo.",
                    description="Repository inspection tools.",
                ),
                McpServerDefinition(
                    server_id="host",
                    name="Host MCP",
                    transport="http",
                    tool_prefix="mcp.host.",
                    description="Client-backed filesystem and terminal tools.",
                ),
            ],
            tools=[
                McpToolDefinition(
                    tool_name="mcp.repo.search_paths", server_id="repo", kind="search"
                ),
                McpToolDefinition(tool_name="mcp.repo.read_file", server_id="repo", kind="read"),
                McpToolDefinition(
                    tool_name="mcp.host.read_workspace_file",
                    server_id="host",
                    kind="read",
                ),
                McpToolDefinition(
                    tool_name="mcp.host.write_workspace_file",
                    server_id="host",
                    kind="edit",
                ),
                McpToolDefinition(
                    tool_name="mcp.host.python_version",
                    server_id="host",
                    kind="execute",
                ),
                McpToolDefinition(
                    tool_name="mcp.host.kill_demo_process",
                    server_id="host",
                    kind="execute",
                ),
            ],
        ),
    ]


def _build_projection_maps() -> tuple[FileSystemProjectionMap, ...]:
    return (
        FileSystemProjectionMap(
            read_tool_names=frozenset({"mcp.repo.read_file", "mcp.host.read_workspace_file"}),
            write_tool_names=frozenset({"mcp.host.write_workspace_file"}),
        ),
    )


@dataclass(slots=True)
class ClientBinding:
    client: AcpClient | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class DemoModelsProvider:
    def get_model_state(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str | DeferredToolRequests],
    ) -> ModelSelectionState:
        del agent
        available_models = _available_models()
        default_model_id = available_models[0].model_id
        current_model_id = str(session.config_values.get("model", default_model_id))
        valid_model_ids = {model.model_id for model in available_models}
        if current_model_id not in valid_model_ids:
            current_model_id = default_model_id
        return ModelSelectionState(
            available_models=available_models,
            current_model_id=current_model_id,
        )

    def set_model(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str | DeferredToolRequests],
        model_id: str,
    ) -> ModelSelectionState:
        session.config_values["model"] = model_id
        return self.get_model_state(session, agent)


@dataclass(slots=True, frozen=True, kw_only=True)
class DemoModesProvider:
    def get_mode_state(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str | DeferredToolRequests],
    ) -> ModeState:
        del agent
        current_mode_id = str(session.config_values.get("mode", "chat"))
        return ModeState(
            modes=[
                SessionMode(
                    id="chat",
                    name="Chat",
                    description="Hide MCP tools and keep the agent conversational.",
                ),
                SessionMode(
                    id="review",
                    name="Review",
                    description="Expose MCP tools, host backends, and review-oriented flows.",
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
class DemoConfigOptionsProvider:
    def get_config_options(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str | DeferredToolRequests],
    ) -> list[ConfigOption]:
        del agent
        verbose_notes = bool(session.config_values.get("verbose_notes", False))
        workspace_scope = str(session.config_values.get("workspace_scope", "repo"))
        return [
            SessionConfigOptionBoolean(
                id="verbose_notes",
                name="Verbose Notes",
                category="demo",
                description="Show full note bodies instead of short previews.",
                type="boolean",
                current_value=verbose_notes,
            ),
            SessionConfigOptionSelect(
                id="workspace_scope",
                name="Workspace Scope",
                category="demo",
                description="Default scope for workspace file reads.",
                type="select",
                current_value=workspace_scope,
                options=[
                    SessionConfigSelectOption(value="repo", name="Repo"),
                    SessionConfigSelectOption(value="host", name="Host"),
                ],
            ),
        ]

    def set_config_option(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str | DeferredToolRequests],
        config_id: str,
        value: str | bool,
    ) -> list[ConfigOption] | None:
        if config_id == "verbose_notes" and isinstance(value, bool):
            session.config_values["verbose_notes"] = value
            return self.get_config_options(session, agent)
        if config_id == "workspace_scope" and isinstance(value, str):
            session.config_values["workspace_scope"] = value
            return self.get_config_options(session, agent)
        return None


@dataclass(slots=True, frozen=True, kw_only=True)
class DemoPlanProvider:
    client_binding: ClientBinding

    def get_plan(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str | DeferredToolRequests],
    ) -> list[PlanEntry]:
        del agent
        current_mode = str(session.config_values.get("mode", "chat"))
        current_model = str(session.config_values.get("model", "router"))
        workspace_scope = str(session.config_values.get("workspace_scope", "repo"))
        host_bound = self.client_binding.client is not None
        return [
            PlanEntry(content=f"mode:{current_mode}", priority="high", status="in_progress"),
            PlanEntry(content=f"model:{current_model}", priority="medium", status="pending"),
            PlanEntry(content=f"scope:{workspace_scope}", priority="medium", status="pending"),
            PlanEntry(
                content=f"host_bound:{str(host_bound).lower()}",
                priority="low",
                status="pending",
            ),
        ]


@dataclass(slots=True, frozen=True, kw_only=True)
class DemoApprovalStateProvider:
    client_binding: ClientBinding

    def get_approval_state(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str | DeferredToolRequests],
    ) -> dict[str, JsonValue]:
        del agent
        remembered = session.metadata.get(_APPROVAL_POLICIES_KEY)
        remembered_policy_count = len(remembered) if isinstance(remembered, dict) else 0
        return {
            "host_context_bound": self.client_binding.client is not None,
            "mode": str(session.config_values.get("mode", "chat")),
            "remembered_policy_count": remembered_policy_count,
            "workspace_scope": str(session.config_values.get("workspace_scope", "repo")),
        }


@dataclass(slots=True, kw_only=True)
class DemoAgentSource:
    client_binding: ClientBinding
    capability_bridges: list[
        HookBridge | HistoryProcessorBridge | PrepareToolsBridge[None] | McpBridge
    ]

    async def get_agent(
        self, session: AcpSessionContext
    ) -> Agent[None, str | DeferredToolRequests]:
        builder = AgentBridgeBuilder(
            session=session,
            capability_bridges=self.capability_bridges,
        )
        contributions = builder.build(
            contextual_history_processors=[_contextual_history],
            plain_history_processors=[_trim_history],
        )
        host_context = None
        if self.client_binding.client is not None:
            host_context = ClientHostContext.from_session(
                client=self.client_binding.client,
                session=session,
            )

        agent = Agent(
            _ROUTER_MODEL,
            name="acpkit_structured_demo",
            output_type=[str, DeferredToolRequests],
            capabilities=contributions.capabilities,
            history_processors=contributions.history_processors,
            system_prompt=(
                "You are the ACP Kit structured demo agent. Use tools aggressively. "
                "Plain note tools are always available. MCP-style repo and host tools appear in "
                "`review` mode. Mutating tools may require approval; let the host flow decide. "
                "If the current model is `direct`, answer plainly without inventing tool calls."
            ),
        )

        def list_notes_text() -> str:
            verbose_notes = bool(session.config_values.get("verbose_notes", False))
            rendered_notes = []
            for name, content in sorted(_DEMO_NOTES.items()):
                preview = content if verbose_notes else _truncate_text(content, limit=80)
                rendered_notes.append(f"- {name}: {preview}")
            if not rendered_notes:
                return "No demo notes are stored yet."
            return "\n".join(("Available demo notes:", *rendered_notes))

        @agent.tool_plain
        def fetch_supported_capabilities() -> str:
            """Return the adapter surfaces exercised by the structured demo."""

            models = ", ".join(model.model_id for model in _available_models())
            return "\n".join(
                (
                    "Structured demo surfaces:",
                    "- session-aware factory via AgentSource",
                    "- file-backed session persistence",
                    "- session-local models, modes, config options, and plan updates",
                    "- native deferred approvals with remembered choices",
                    "- hook, history, prepare-tools, and MCP bridges",
                    "- filesystem-aware diff projection for repo and host file tools",
                    f"- host context bound: {host_context is not None}",
                    f"- available models: {models}",
                )
            )

        @agent.tool_plain
        def list_notes() -> str:
            """List demo notes, honoring the current `verbose_notes` config option."""

            return list_notes_text()

        @agent.tool_plain
        def read_note(name: str) -> str:
            """Read one demo note by name."""

            if not name.strip():
                return list_notes_text()
            note_name = _normalize_note_name(name)
            note = _DEMO_NOTES.get(note_name)
            if note is None:
                return f"No note named `{note_name}`.\n\n{list_notes_text()}"
            if bool(session.config_values.get("verbose_notes", False)):
                return note
            return _truncate_text(note, limit=120)

        @agent.tool_plain
        def search_notes(query: str) -> str:
            """Search demo notes by name or content."""

            normalized_query = query.strip().lower()
            if not normalized_query:
                return list_notes_text()
            matches = [
                name
                for name, content in sorted(_DEMO_NOTES.items())
                if normalized_query in name or normalized_query in content.lower()
            ]
            if not matches:
                return f"No demo notes matched `{query}`.\n\n{list_notes_text()}"
            return "\n".join(matches)

        @agent.tool_plain(requires_approval=True)
        def update_note(name: str, content: str) -> str:
            """Create or replace a demo note. Always routed through ACP approval."""

            note_name = _normalize_note_name(name)
            _DEMO_NOTES[note_name] = content.strip()
            return f"Updated note `{note_name}`."

        @agent.tool_plain(requires_approval=True)
        def delete_note(name: str) -> str:
            """Delete a demo note. Always routed through ACP approval."""

            note_name = _normalize_note_name(name)
            removed = _DEMO_NOTES.pop(note_name, None)
            if removed is None:
                return f"Note `{note_name}` was already absent."
            return f"Deleted note `{note_name}`."

        @agent.tool_plain(name="mcp.repo.search_paths")
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

        @agent.tool_plain(name="mcp.repo.read_file")
        def read_repo_file(path: str, max_chars: int = 4000) -> str:
            """Read a repository file relative to the repo root."""

            if max_chars <= 0:
                raise ValueError("max_chars must be positive.")
            file_path = _resolve_repo_path(path)
            text = file_path.read_text(encoding="utf-8")
            return _truncate_text(text, limit=max_chars)

        if host_context is not None:

            @agent.tool(name="mcp.host.read_workspace_file")
            async def read_workspace_file(ctx: RunContext[None], path: str) -> str:
                """Read a file through the ACP client-backed filesystem backend."""

                del ctx
                response = await host_context.filesystem.read_text_file(path)
                return response.content

            @agent.tool(name="mcp.host.write_workspace_file", requires_approval=True)
            async def write_workspace_file(
                ctx: RunContext[None],
                path: str,
                content: str,
            ) -> str:
                """Write a file through the ACP client-backed filesystem backend."""

                del ctx
                response = await host_context.filesystem.write_text_file(path, content)
                if response is None:
                    return f"No write response returned for `{path}`."
                return f"Wrote workspace file `{path}`."

            @agent.tool(name="mcp.host.python_version")
            async def python_version(ctx: RunContext[None]) -> str:
                """Run `python -V` through the ACP client-backed terminal backend."""

                del ctx
                terminal = await host_context.terminal.create_terminal(
                    "python",
                    args=["-V"],
                    cwd=str(session.cwd),
                    output_byte_limit=4096,
                )
                await host_context.terminal.wait_for_terminal_exit(terminal.terminal_id)
                output = await host_context.terminal.terminal_output(terminal.terminal_id)
                await host_context.terminal.release_terminal(terminal.terminal_id)
                return output.output

            @agent.tool(name="mcp.host.kill_demo_process")
            async def kill_demo_process(ctx: RunContext[None]) -> str:
                """Create a short-lived process and terminate it through the host backend."""

                del ctx
                terminal = await host_context.terminal.create_terminal(
                    "python",
                    args=["-c", "import time; time.sleep(30)"],
                    cwd=str(session.cwd),
                    output_byte_limit=512,
                )
                await host_context.terminal.kill_terminal(terminal.terminal_id)
                result = await host_context.terminal.wait_for_terminal_exit(terminal.terminal_id)
                await host_context.terminal.release_terminal(terminal.terminal_id)
                if result.signal is not None:
                    return f"Terminated demo process with signal `{result.signal}`."
                return f"Demo process exited with code `{result.exit_code}`."

        return agent

    async def get_deps(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str | DeferredToolRequests],
    ) -> None:
        del session, agent
        return None


@dataclass(slots=True, kw_only=True)
class BoundDemoAgent:
    client_binding: ClientBinding
    delegate: AcpAgent

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        return await self.delegate.initialize(
            protocol_version=protocol_version,
            client_capabilities=client_capabilities,
            client_info=client_info,
            **kwargs,
        )

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        return await self.delegate.new_session(cwd=cwd, mcp_servers=mcp_servers, **kwargs)

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse | None:
        return await self.delegate.load_session(
            cwd=cwd,
            session_id=session_id,
            mcp_servers=mcp_servers,
            **kwargs,
        )

    async def list_sessions(
        self,
        cursor: str | None = None,
        cwd: str | None = None,
        **kwargs: Any,
    ) -> ListSessionsResponse:
        return await self.delegate.list_sessions(cursor=cursor, cwd=cwd, **kwargs)

    async def set_session_mode(
        self,
        mode_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> SetSessionModeResponse | None:
        return await self.delegate.set_session_mode(
            mode_id=mode_id, session_id=session_id, **kwargs
        )

    async def set_session_model(
        self,
        model_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> SetSessionModelResponse | None:
        return await self.delegate.set_session_model(
            model_id=model_id,
            session_id=session_id,
            **kwargs,
        )

    async def set_config_option(
        self,
        config_id: str,
        session_id: str,
        value: str | bool,
        **kwargs: Any,
    ) -> SetSessionConfigOptionResponse | None:
        return await self.delegate.set_config_option(
            config_id=config_id,
            session_id=session_id,
            value=value,
            **kwargs,
        )

    async def authenticate(self, method_id: str, **kwargs: Any) -> AuthenticateResponse | None:
        return await self.delegate.authenticate(method_id=method_id, **kwargs)

    async def prompt(
        self,
        prompt: list[PromptBlock],
        session_id: str,
        message_id: str | None = None,
        **kwargs: Any,
    ) -> PromptResponse:
        return await self.delegate.prompt(
            prompt=prompt,
            session_id=session_id,
            message_id=message_id,
            **kwargs,
        )

    async def fork_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> ForkSessionResponse:
        return await self.delegate.fork_session(
            cwd=cwd,
            session_id=session_id,
            mcp_servers=mcp_servers,
            **kwargs,
        )

    async def resume_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        return await self.delegate.resume_session(
            cwd=cwd,
            session_id=session_id,
            mcp_servers=mcp_servers,
            **kwargs,
        )

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse | None:
        return await self.delegate.close_session(session_id=session_id, **kwargs)

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        await self.delegate.cancel(session_id=session_id, **kwargs)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self.delegate.ext_method(method, params)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        await self.delegate.ext_notification(method, params)

    def on_connect(self, conn: AcpClient) -> None:
        self.client_binding.client = conn
        self.delegate.on_connect(conn)


def build_server_agent() -> AcpAgent:
    client_binding = ClientBinding()
    capability_bridges = _build_bridges()
    delegate = create_acp_agent(
        agent_source=DemoAgentSource(
            client_binding=client_binding,
            capability_bridges=capability_bridges,
        ),
        config=AdapterConfig(
            agent_name="acpkit_structured_demo",
            agent_title="ACP Kit Structured Demo",
            approval_bridge=NativeApprovalBridge(enable_persistent_choices=True),
            approval_state_provider=DemoApprovalStateProvider(client_binding=client_binding),
            capability_bridges=list(capability_bridges),
            config_options_provider=DemoConfigOptionsProvider(),
            models_provider=DemoModelsProvider(),
            modes_provider=DemoModesProvider(),
            plan_provider=DemoPlanProvider(client_binding=client_binding),
            projection_maps=_build_projection_maps(),
            session_store=FileSessionStore(_SESSION_STORE_DIR),
        ),
    )
    return BoundDemoAgent(client_binding=client_binding, delegate=delegate)


def main() -> None:
    asyncio.run(run_agent(build_server_agent()))


if __name__ == "__main__":
    main()
