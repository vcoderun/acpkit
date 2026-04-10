from __future__ import annotations as _annotations

from dataclasses import dataclass, field
from typing import Literal

from acp.schema import (
    McpCapabilities,
    SessionConfigOptionBoolean,
    SessionConfigOptionSelect,
    ToolKind,
)

from ..agent_types import RuntimeAgent
from ..providers import ConfigOption
from ..session.state import AcpSessionContext, JsonValue
from .base import CapabilityBridge

McpApprovalScope = Literal["tool", "server", "prefix"]
McpTransport = Literal["http", "sse"]

__all__ = (
    "McpBridge",
    "McpServerDefinition",
    "McpToolDefinition",
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
            option_values = {choice.value for choice in option.options}
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
