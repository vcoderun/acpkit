from __future__ import annotations as _annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, TypeAlias

from acp.schema import (
    AvailableCommand,
    AvailableCommandInput,
    SessionConfigOptionBoolean,
    SessionConfigOptionSelect,
    SessionMode,
    SessionModelState,
    SessionModeState,
    UnstructuredCommandInput,
)
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.tools import Tool
from pydantic_ai.toolsets._dynamic import DynamicToolset
from pydantic_ai.toolsets.combined import CombinedToolset
from pydantic_ai.toolsets.wrapper import WrapperToolset

from .._slash_commands import (
    HOOKS_COMMAND_NAME,
    MCP_SERVERS_COMMAND_NAME,
    MODEL_COMMAND_NAME,
    THINKING_COMMAND_NAME,
    TOOLS_COMMAND_NAME,
    validate_mode_command_ids,
)
from ..hook_projection import HookProjectionMap
from ..session.state import AcpSessionContext, JsonValue
from .hook_introspection import RegisteredHookInfo

__all__ = (
    "McpServerInfo",
    "SlashCommand",
    "ToolInfo",
    "build_available_commands",
    "extract_session_mcp_servers",
    "list_agent_mcp_servers",
    "list_agent_tools",
    "parse_slash_command",
    "render_hook_listing",
    "render_mcp_server_listing",
    "render_mode_message",
    "render_model_message",
    "render_thinking_message",
    "render_tool_listing",
    "validate_mode_command_ids",
)

_INTERNAL_TOOL_PREFIX = "acp_"
ConfigOptionType: TypeAlias = SessionConfigOptionSelect | SessionConfigOptionBoolean


@dataclass(slots=True, frozen=True, kw_only=True)
class SlashCommand:
    name: str
    argument: str | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class ToolInfo:
    name: str
    description: str | None
    requires_approval: bool


@dataclass(slots=True, frozen=True, kw_only=True)
class McpServerInfo:
    name: str
    transport: str
    target: str
    source: str


def build_available_commands(
    *,
    mode_state: SessionModeState | None,
    model_state: SessionModelState | None,
    config_options: Sequence[ConfigOptionType] | None,
) -> list[AvailableCommand]:
    commands: list[AvailableCommand] = []
    if mode_state is not None:
        validate_mode_command_ids(mode.id for mode in mode_state.available_modes)
        commands.extend(_mode_commands(mode_state.available_modes))
    if model_state is not None:
        commands.append(
            AvailableCommand(
                name=MODEL_COMMAND_NAME,
                description="Show the current session model, or set it with a provider:model value.",
                input=AvailableCommandInput(
                    root=UnstructuredCommandInput(hint="provider:model or codex:model")
                ),
            )
        )
    if _has_config_option(config_options, THINKING_COMMAND_NAME):
        commands.append(
            AvailableCommand(
                name=THINKING_COMMAND_NAME,
                description="Show or set the current thinking effort.",
                input=AvailableCommandInput(
                    root=UnstructuredCommandInput(hint="default|off|minimal|low|medium|high|xhigh")
                ),
            )
        )
    commands.extend(
        [
            AvailableCommand(
                name=TOOLS_COMMAND_NAME,
                description="List the tools currently registered on the active agent.",
            ),
            AvailableCommand(
                name=HOOKS_COMMAND_NAME,
                description="List the registered Hooks capability callbacks visible on the active agent.",
            ),
            AvailableCommand(
                name=MCP_SERVERS_COMMAND_NAME,
                description="List MCP servers extracted from the active agent and session metadata.",
            ),
        ]
    )
    return commands


def _mode_commands(modes: Sequence[SessionMode]) -> list[AvailableCommand]:
    return [
        AvailableCommand(
            name=mode.id,
            description=mode.description or f"Switch the active session into {mode.name} mode.",
        )
        for mode in modes
    ]


def _has_config_option(
    config_options: Sequence[ConfigOptionType] | None,
    option_id: str,
) -> bool:
    if config_options is None:
        return False
    return any(option.id == option_id for option in config_options)


def parse_slash_command(prompt_text: str) -> SlashCommand | None:
    stripped = prompt_text.strip()
    if not stripped.startswith("/"):
        return None
    command_text = stripped[1:]
    if not command_text.strip():
        return None
    name, _, remainder = command_text.partition(" ")
    normalized_name = name.strip().lower()
    if not normalized_name:
        return None
    argument = remainder.strip() or None
    return SlashCommand(name=normalized_name, argument=argument)


def render_mode_message(current_mode_id: str | None) -> str:
    if current_mode_id is None:
        return "Current mode: unavailable"
    return f"Current mode: {current_mode_id}"


def render_model_message(current_model_id: str | None) -> str:
    if current_model_id is None:
        return "Current model: unavailable"
    return f"Current model: {current_model_id}"


def render_thinking_message(current_thinking_value: str | None) -> str:
    if current_thinking_value is None:
        return "Thinking effort: unavailable"
    return f"Thinking effort: {current_thinking_value}"


def list_agent_tools(agent: PydanticAgent[Any, Any]) -> list[ToolInfo]:
    function_toolset = getattr(agent, "_function_toolset", None)
    tools = getattr(function_toolset, "tools", None)
    if not isinstance(tools, dict):
        return []
    tool_infos: list[ToolInfo] = []
    string_items = [(name, tool) for name, tool in tools.items() if isinstance(name, str)]
    for name, tool in sorted(string_items, key=lambda item: item[0]):
        if name.startswith(_INTERNAL_TOOL_PREFIX):
            continue
        if not isinstance(tool, Tool):
            continue
        description = tool.description if isinstance(tool.description, str) else None
        tool_infos.append(
            ToolInfo(
                name=name,
                description=description,
                requires_approval=tool.requires_approval,
            )
        )
    return tool_infos


def render_tool_listing(tool_infos: list[ToolInfo]) -> str:
    if not tool_infos:
        return "No tools are currently registered."
    lines = ["Available tools:"]
    for tool_info in tool_infos:
        suffix = " [approval]" if tool_info.requires_approval else ""
        if tool_info.description is not None:
            lines.append(f"- {tool_info.name}{suffix}: {tool_info.description}")
        else:
            lines.append(f"- {tool_info.name}{suffix}")
    return "\n".join(lines)


def render_hook_listing(
    hook_infos: list[RegisteredHookInfo],
    *,
    projection_map: HookProjectionMap | None,
) -> str:
    if not hook_infos:
        return "No Hooks capability callbacks are currently registered."
    active_projection_map = HookProjectionMap() if projection_map is None else projection_map
    lines = ["Registered hooks:"]
    for hook_info in hook_infos:
        event_label = active_projection_map.event_labels.get(hook_info.event_id, hook_info.event_id)
        tool_filters = (
            f" [tools: {', '.join(hook_info.tool_filters)}]" if hook_info.tool_filters else ""
        )
        lines.append(f"- {event_label}: {hook_info.hook_name}{tool_filters}")
    return "\n".join(lines)


def extract_session_mcp_servers(
    session: AcpSessionContext,
    *,
    agent: PydanticAgent[Any, Any] | None = None,
) -> list[McpServerInfo]:
    server_infos: list[McpServerInfo] = []
    seen: set[tuple[str, str, str]] = set()
    if agent is not None:
        for server_info in list_agent_mcp_servers(agent):
            dedupe_key = (
                server_info.name,
                server_info.transport,
                server_info.target,
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            server_infos.append(server_info)
    for raw_server in session.mcp_servers:
        server_info = _mcp_server_info_from_session_payload(raw_server)
        if server_info is None:
            continue
        dedupe_key = (
            server_info.name,
            server_info.transport,
            server_info.target,
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        server_infos.append(server_info)
    metadata_root = session.metadata.get("pydantic_acp")
    if not isinstance(metadata_root, dict):
        return server_infos
    mcp_metadata = metadata_root.get("mcp")
    if not isinstance(mcp_metadata, dict):
        return server_infos
    metadata_servers = mcp_metadata.get("servers")
    if not isinstance(metadata_servers, list):
        return server_infos
    for raw_server in metadata_servers:
        server_info = _mcp_server_info_from_bridge_metadata(raw_server)
        if server_info is None:
            continue
        dedupe_key = (
            server_info.name,
            server_info.transport,
            server_info.target,
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        server_infos.append(server_info)
    return server_infos


def list_agent_mcp_servers(agent: PydanticAgent[Any, Any]) -> list[McpServerInfo]:
    server_infos: list[McpServerInfo] = []
    seen: set[tuple[str, str, str]] = set()
    for toolset in getattr(agent, "toolsets", ()):
        for server_info in _iter_mcp_server_infos(toolset):
            dedupe_key = (
                server_info.name,
                server_info.transport,
                server_info.target,
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            server_infos.append(server_info)
    return server_infos


def render_mcp_server_listing(server_infos: list[McpServerInfo]) -> str:
    if not server_infos:
        return "No MCP servers are currently attached."
    lines = ["MCP servers:"]
    for server_info in server_infos:
        lines.append(
            f"- {server_info.name} ({server_info.transport}, {server_info.source}): "
            f"{server_info.target}"
        )
    return "\n".join(lines)


def _mcp_server_info_from_session_payload(
    raw_server: dict[str, JsonValue],
) -> McpServerInfo | None:
    name = raw_server.get("name")
    if not isinstance(name, str) or not name:
        return None
    transport = raw_server.get("transport")
    if not isinstance(transport, str) or not transport:
        return None
    if transport == "stdio":
        command = raw_server.get("command")
        args = raw_server.get("args")
        rendered_args = (
            " ".join(item for item in args if isinstance(item, str))
            if isinstance(args, list)
            else ""
        )
        target = command if isinstance(command, str) else "<stdio>"
        if rendered_args:
            target = f"{target} {rendered_args}"
    else:
        url = raw_server.get("url")
        target = url if isinstance(url, str) and url else f"<{transport}>"
    return McpServerInfo(
        name=name,
        transport=transport,
        target=target,
        source="session",
    )


def _mcp_server_info_from_bridge_metadata(
    raw_server: JsonValue,
) -> McpServerInfo | None:
    raw_server_dict = _string_key_dict(raw_server)
    if raw_server_dict is None:
        return None
    name = raw_server_dict.get("name")
    transport = raw_server_dict.get("transport")
    if not isinstance(name, str) or not isinstance(transport, str):
        return None
    url = raw_server_dict.get("url")
    tool_prefix = raw_server_dict.get("tool_prefix")
    description = raw_server_dict.get("description")
    target_parts = [
        value for value in (url, tool_prefix, description) if isinstance(value, str) and value
    ]
    target = " | ".join(target_parts) if target_parts else f"<{transport}>"
    return McpServerInfo(
        name=name,
        transport=transport,
        target=target,
        source="bridge",
    )


def _iter_mcp_server_infos(toolset: Any) -> list[McpServerInfo]:
    if isinstance(toolset, CombinedToolset):
        server_infos: list[McpServerInfo] = []
        for nested_toolset in toolset.toolsets:
            server_infos.extend(_iter_mcp_server_infos(nested_toolset))
        return server_infos
    if isinstance(toolset, WrapperToolset):
        return _iter_mcp_server_infos(toolset.wrapped)
    if isinstance(toolset, DynamicToolset):
        current_toolset = getattr(toolset, "_toolset", None)
        if current_toolset is None:
            return []
        return _iter_mcp_server_infos(current_toolset)
    if _is_mcp_server_stdio(toolset):
        server_info = _mcp_server_info_from_stdio_toolset(toolset)
        return [server_info] if server_info is not None else []
    if _is_mcp_server_http(toolset):
        server_info = _mcp_server_info_from_http_toolset(toolset)
        return [server_info] if server_info is not None else []
    return []


def _is_mcp_server_stdio(toolset: Any) -> bool:
    return (
        type(toolset).__module__ == "pydantic_ai.mcp" and type(toolset).__name__ == "MCPServerStdio"
    )


def _is_mcp_server_http(toolset: Any) -> bool:
    return type(toolset).__module__ == "pydantic_ai.mcp" and type(toolset).__name__ in {
        "MCPServerSSE",
        "MCPServerStreamableHTTP",
    }


def _mcp_server_info_from_stdio_toolset(toolset: Any) -> McpServerInfo | None:
    command = getattr(toolset, "command", None)
    args = getattr(toolset, "args", None)
    if not isinstance(command, str) or not command:
        return None
    rendered_args = (
        " ".join(item for item in args if isinstance(item, str)) if isinstance(args, list) else ""
    )
    target = command if not rendered_args else f"{command} {rendered_args}"
    tool_prefix = getattr(toolset, "tool_prefix", None)
    if isinstance(tool_prefix, str) and tool_prefix:
        target = f"{target} | prefix={tool_prefix}"
    return McpServerInfo(
        name=_toolset_name(toolset, fallback=command),
        transport="stdio",
        target=target,
        source="agent",
    )


def _mcp_server_info_from_http_toolset(toolset: Any) -> McpServerInfo | None:
    url = getattr(toolset, "url", None)
    if not isinstance(url, str) or not url:
        return None
    transport = "sse" if type(toolset).__name__ == "MCPServerSSE" else "http"
    target = url
    tool_prefix = getattr(toolset, "tool_prefix", None)
    if isinstance(tool_prefix, str) and tool_prefix:
        target = f"{target} | prefix={tool_prefix}"
    return McpServerInfo(
        name=_toolset_name(toolset, fallback=url),
        transport=transport,
        target=target,
        source="agent",
    )


def _toolset_name(toolset: Any, *, fallback: str) -> str:
    for attribute_name in ("id", "_id"):
        value = getattr(toolset, attribute_name, None)
        if isinstance(value, str) and value:
            return value
    return fallback


def _string_key_dict(value: JsonValue) -> dict[str, JsonValue] | None:
    if not isinstance(value, dict):
        return None
    string_key_items = [(key, item) for key, item in value.items() if isinstance(key, str)]
    if len(string_key_items) != len(value):
        return None
    return dict(string_key_items)
