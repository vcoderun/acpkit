from __future__ import annotations as _annotations

from dataclasses import dataclass, field
from typing import Any, Literal, cast

from acp.schema import (
    McpCapabilities,
    SessionConfigOptionBoolean,
    SessionConfigOptionSelect,
    SessionConfigSelectGroup,
    ToolKind,
)
from pydantic_ai.capabilities import AbstractCapability, Toolset
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import AbstractToolset

from ..agent_types import RuntimeAgent
from ..providers import ConfigOption
from ..session.state import AcpSessionContext, JsonValue
from .base import CapabilityBridge

McpApprovalScope = Literal["tool", "server", "prefix"]
McpTransport = Literal["http", "sse"]
SessionMcpToolErrorBehavior = Literal["retry", "error"]

__all__ = (
    "McpBridge",
    "McpServerDefinition",
    "McpToolDefinition",
    "SessionMcpBridge",
)


@dataclass(slots=True, frozen=True, kw_only=True)
class McpServerDefinition:
    server_id: str
    name: str
    transport: McpTransport
    url: str | None = None
    description: str | None = None
    tool_prefix: str | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class McpToolDefinition:
    tool_name: str
    server_id: str
    kind: ToolKind = "execute"


@dataclass(slots=True, kw_only=True)
class SessionMcpBridge(CapabilityBridge):
    """Attach ACP client-provided MCP servers to the Pydantic AI agent run.

    ACP clients may pass MCP server definitions during `session/new`, `session/load`,
    `session/fork`, or `session/resume`. The adapter persists those definitions on
    `AcpSessionContext.mcp_servers`; this bridge turns them into a Pydantic AI
    `MCPToolset` capability for the active session.
    """

    metadata_key: str | None = "session_mcp"
    include_instructions: bool = True
    include_return_schema: bool | None = None
    cache_tools: bool = True
    cache_resources: bool = True
    cache_prompts: bool = True
    tool_error_behavior: SessionMcpToolErrorBehavior = "retry"
    max_retries: int | None = None
    allowed_tools: list[str] | None = None
    require_approval: bool = False
    tool_name_prefixes: frozenset[str] = frozenset()
    toolset_id_prefix: str = "acp-session-mcp"
    advertise_http: bool = True
    advertise_sse: bool = True

    def build_agent_capabilities(
        self,
        session: AcpSessionContext,
    ) -> tuple[AbstractCapability[Any], ...]:
        return (_SessionMcpCapability(bridge=self, session=session),)

    def get_mcp_capabilities(self, agent: RuntimeAgent | None = None) -> McpCapabilities | None:
        del agent
        if not self.advertise_http and not self.advertise_sse:
            return None
        return McpCapabilities(http=self.advertise_http, sse=self.advertise_sse)

    def get_session_metadata(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> dict[str, JsonValue] | None:
        del agent
        servers = _session_mcp_metadata(session.mcp_servers)
        if not servers:
            return None
        return {
            "allowed_tools": _json_string_list(self.allowed_tools),
            "cache_prompts": self.cache_prompts,
            "cache_resources": self.cache_resources,
            "cache_tools": self.cache_tools,
            "include_instructions": self.include_instructions,
            "include_return_schema": self.include_return_schema,
            "require_approval": self.require_approval,
            "server_count": len(servers),
            "servers": servers,
            "tool_error_behavior": self.tool_error_behavior,
            "tool_name_prefixes": _json_string_list(self.tool_name_prefixes),
        }

    def get_tool_kind(self, tool_name: str, raw_input: JsonValue | None = None) -> ToolKind | None:
        del raw_input
        if any(tool_name.startswith(prefix) for prefix in self.tool_name_prefixes):
            return "execute"
        return None


class _SessionMcpCapability(AbstractCapability[Any]):
    """Resolve the mutable ACP session's MCP surface once per agent run."""

    def __init__(self, *, bridge: SessionMcpBridge, session: AcpSessionContext) -> None:
        self._bridge = bridge
        self._session = session

    async def for_run(self, ctx: RunContext[Any]) -> AbstractCapability[Any]:
        del ctx
        return _session_mcp_capability(self._bridge, self._session) or self

    @classmethod
    def get_serialization_name(cls) -> None:
        return None


def _session_mcp_capability(
    bridge: SessionMcpBridge,
    session: AcpSessionContext,
) -> AbstractCapability[Any] | None:
    config = _session_mcp_config(session.mcp_servers)
    if config is None:
        return None

    try:
        from pydantic_ai.capabilities import MCP
        from pydantic_ai.mcp import MCPToolset
    except ImportError as exc:
        raise ImportError(
            "Pydantic AI MCP support is required for SessionMcpBridge. "
            "Install `pydantic-ai-slim[mcp]` or a pydantic-ai distribution with MCP extras.",
        ) from exc

    capability_id = f"{bridge.toolset_id_prefix}:{session.session_id}"
    toolset = MCPToolset(
        cast("Any", config),
        id=capability_id,
        max_retries=bridge.max_retries,
        tool_error_behavior=bridge.tool_error_behavior,
        cache_tools=bridge.cache_tools,
        cache_resources=bridge.cache_resources,
        cache_prompts=bridge.cache_prompts,
        include_instructions=bridge.include_instructions,
        include_return_schema=bridge.include_return_schema,
    )
    if bridge.require_approval:
        approval_toolset: AbstractToolset[Any] = toolset
        if bridge.allowed_tools is not None:
            allowed_tools = frozenset(bridge.allowed_tools)
            approval_toolset = approval_toolset.filtered(
                lambda _ctx, tool_def: tool_def.name in allowed_tools
            )
        return Toolset(approval_toolset.approval_required(), id=capability_id)
    return MCP(
        native=False,
        local=toolset,
        id=capability_id,
        allowed_tools=bridge.allowed_tools,
    )


@dataclass(slots=True, kw_only=True)
class McpBridge(CapabilityBridge):
    metadata_key: str | None = "mcp"
    approval_policy_scope: McpApprovalScope = "tool"
    config_options: list[ConfigOption] = field(default_factory=list)
    servers: list[McpServerDefinition] = field(default_factory=list)
    tools: list[McpToolDefinition] = field(default_factory=list)

    def get_mcp_capabilities(self, agent: RuntimeAgent | None = None) -> McpCapabilities | None:
        del agent
        if not self.servers:
            return None
        return McpCapabilities(
            http=any(server.transport == "http" for server in self.servers),
            sse=any(server.transport == "sse" for server in self.servers),
        )

    def get_session_metadata(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> dict[str, JsonValue] | None:
        if not self.servers and not self.config_options:
            return None
        servers: list[JsonValue] = [
            {
                "description": server.description,
                "name": server.name,
                "server_id": server.server_id,
                "tool_prefix": server.tool_prefix,
                "transport": server.transport,
                "url": server.url,
            }
            for server in self.servers
        ]
        metadata: dict[str, JsonValue] = {"approval_policy_scope": self.approval_policy_scope}
        if servers:
            metadata["servers"] = servers
        if self.config_options:
            config_option_ids: list[JsonValue] = [option.id for option in self.config_options]
            metadata["config_option_ids"] = config_option_ids
            current_config: dict[str, JsonValue] = {}
            for option in self.get_config_options(session, agent) or []:
                current_config[option.id] = option.current_value
            metadata["config"] = current_config
        return metadata

    def get_config_options(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> list[ConfigOption] | None:
        del agent
        if not self.config_options:
            return None
        return [self._sync_config_option(option, session) for option in self.config_options]

    def get_tool_kind(self, tool_name: str, raw_input: JsonValue | None = None) -> ToolKind | None:
        del raw_input
        for tool in self.tools:
            if tool.tool_name == tool_name:
                return tool.kind
        for server in self.servers:
            if server.tool_prefix is not None and tool_name.startswith(server.tool_prefix):
                return "execute"
        return None

    def get_approval_policy_key(
        self,
        tool_name: str,
        raw_input: JsonValue | None = None,
    ) -> str | None:
        del raw_input
        if self.approval_policy_scope == "tool":
            return None

        explicit_tool = self._find_tool(tool_name)
        if explicit_tool is not None:
            if self.approval_policy_scope == "server":
                return f"mcp:server:{explicit_tool.server_id}"
            return self._prefix_policy_key(explicit_tool.tool_name)

        matching_server = self._find_server_for_tool(tool_name)
        if matching_server is None:
            return None
        if self.approval_policy_scope == "server":
            return f"mcp:server:{matching_server.server_id}"
        return self._prefix_policy_key(tool_name)

    def set_config_option(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
        config_id: str,
        value: str | bool,
    ) -> list[ConfigOption] | None:
        option = self._find_config_option(config_id)
        if option is None:
            return None
        if isinstance(option, SessionConfigOptionBoolean):
            if not isinstance(value, bool):
                return None
        elif isinstance(option, SessionConfigOptionSelect):
            if not isinstance(value, str):
                return None
            option_values = _select_option_values(option)
            if value not in option_values:
                return None
        session.config_values[config_id] = value
        return self.get_config_options(session, agent)

    def _find_config_option(self, config_id: str) -> ConfigOption | None:
        for option in self.config_options:
            if option.id == config_id:
                return option
        return None

    def _find_server_for_tool(self, tool_name: str) -> McpServerDefinition | None:
        explicit_tool = self._find_tool(tool_name)
        if explicit_tool is not None:
            return self._find_server(explicit_tool.server_id)
        for server in self.servers:
            if server.tool_prefix is not None and tool_name.startswith(server.tool_prefix):
                return server
        return None

    def _find_server(self, server_id: str) -> McpServerDefinition | None:
        for server in self.servers:
            if server.server_id == server_id:
                return server
        return None

    def _find_tool(self, tool_name: str) -> McpToolDefinition | None:
        for tool in self.tools:
            if tool.tool_name == tool_name:
                return tool
        return None

    def _prefix_policy_key(self, tool_name: str) -> str:
        matching_server = self._find_server_for_tool(tool_name)
        if matching_server is None or matching_server.tool_prefix is None:
            return f"mcp:tool:{tool_name}"
        return f"mcp:prefix:{matching_server.tool_prefix}"

    def _sync_config_option(
        self,
        option: ConfigOption,
        session: AcpSessionContext,
    ) -> ConfigOption:
        current_value = session.config_values.get(option.id)
        if isinstance(option, SessionConfigOptionBoolean):
            if isinstance(current_value, bool):
                return option.model_copy(update={"current_value": current_value})
            return option
        if isinstance(option, SessionConfigOptionSelect):
            if isinstance(current_value, str):
                return option.model_copy(update={"current_value": current_value})
            return option
        return option


def _select_option_values(option: SessionConfigOptionSelect) -> set[str]:
    values: set[str] = set()
    for item in option.options:
        if isinstance(item, SessionConfigSelectGroup):
            values.update(choice.value for choice in item.options)
        else:
            values.add(item.value)
    return values


def _session_mcp_config(
    servers: list[dict[str, JsonValue]],
) -> dict[str, Any] | None:
    mcp_servers: dict[str, Any] = {}
    for server in servers:
        transport = server.get("transport")
        name = _json_string_value(server.get("name")) or f"server-{len(mcp_servers) + 1}"
        server_name = _unique_session_mcp_name(name, mcp_servers)
        server_config = _session_mcp_server_config(server, transport)
        if server_config is not None:
            mcp_servers[server_name] = server_config
    if not mcp_servers:
        return None
    return {"mcpServers": mcp_servers}


def _session_mcp_server_config(
    server: dict[str, JsonValue],
    transport: JsonValue | None,
) -> dict[str, Any] | None:
    if transport == "stdio":
        command = _json_string_value(server.get("command"))
        if command is None:
            return None
        server_config: dict[str, Any] = {
            "command": command,
            "transport": "stdio",
        }
        args = _json_string_sequence(server.get("args"))
        if args:
            server_config["args"] = args
        env = _json_string_mapping(server.get("env"))
        if env:
            server_config["env"] = env
        return server_config

    if transport == "http" or transport == "sse":
        url = _json_string_value(server.get("url"))
        if url is None:
            return None
        server_config = {
            "transport": transport,
            "url": url,
        }
        headers = _json_string_mapping(server.get("headers"))
        if headers:
            server_config["headers"] = headers
        return server_config

    return None


def _session_mcp_metadata(
    servers: list[dict[str, JsonValue]],
) -> list[JsonValue]:
    metadata: list[JsonValue] = []
    for server in servers:
        transport = server.get("transport")
        if not isinstance(transport, str):
            continue
        name = _json_string_value(server.get("name"))
        server_metadata: dict[str, JsonValue] = {
            "name": name,
            "transport": transport,
        }
        if transport == "stdio":
            server_metadata["args"] = _json_string_sequence(server.get("args"))
            server_metadata["command"] = _json_string_value(server.get("command"))
            server_metadata["env_names"] = _json_string_mapping_keys(server.get("env"))
        elif transport == "http" or transport == "sse":
            server_metadata["header_names"] = _json_string_mapping_keys(server.get("headers"))
            server_metadata["url"] = _json_string_value(server.get("url"))
        metadata.append(server_metadata)
    return metadata


def _unique_session_mcp_name(name: str, existing: dict[str, JsonValue]) -> str:
    if name not in existing:
        return name
    index = 2
    while f"{name}-{index}" in existing:
        index += 1
    return f"{name}-{index}"


def _json_string_value(value: JsonValue | None) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _json_string_sequence(value: JsonValue | None) -> list[JsonValue]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _json_string_list(values: frozenset[str] | list[str] | None) -> list[JsonValue] | None:
    if values is None:
        return None
    result: list[JsonValue] = []
    result.extend(sorted(values))
    return result


def _json_string_mapping(value: JsonValue | None) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item for key, item in value.items() if isinstance(key, str) and isinstance(item, str)
    }


def _json_string_mapping_keys(value: JsonValue | None) -> list[JsonValue]:
    result: list[JsonValue] = []
    result.extend(sorted(_json_string_mapping(value)))
    return result
