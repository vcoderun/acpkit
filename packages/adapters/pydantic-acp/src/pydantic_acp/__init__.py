from __future__ import annotations as _annotations

from .agent_source import AgentFactory, AgentSource, FactoryAgentSource, StaticAgentSource
from .agent_types import RuntimeAgent
from .approvals import ApprovalBridge, NativeApprovalBridge
from .bridges import (
    BufferedCapabilityBridge,
    CapabilityBridge,
    HistoryProcessorBridge,
    HookBridge,
    McpBridge,
    McpServerDefinition,
    McpToolDefinition,
    PrepareToolsBridge,
    PrepareToolsMode,
    ThinkingBridge,
)
from .builders import AgentBridgeBuilder, AgentBridgeContributions
from .config import AdapterConfig
from .hook_projection import HookEvent, HookProjectionMap
from .runtime.hook_introspection import RegisteredHookInfo, list_agent_hooks
from .host import (
    ClientFilesystemBackend,
    ClientHostContext,
    ClientTerminalBackend,
    FilesystemBackend,
    TerminalBackend,
)
from .models import AdapterModel
from .projection import (
    CompositeProjectionMap,
    FileSystemProjectionMap,
    ProjectionMap,
    compose_projection_maps,
)
from .providers import (
    ApprovalStateProvider,
    ConfigOption,
    ConfigOptionsProvider,
    ModelSelectionState,
    ModeState,
    NativePlanPersistenceProvider,
    PlanProvider,
    SessionModelsProvider,
    SessionModesProvider,
)
from .runtime.server import create_acp_agent, run_acp
from .session.state import AcpSessionContext, JsonValue
from .session.store import FileSessionStore, MemorySessionStore, SessionStore

__all__ = (
    "AcpSessionContext",
    "AgentFactory",
    "AgentSource",
    "ApprovalBridge",
    "ApprovalStateProvider",
    "AdapterConfig",
    "AdapterModel",
    "AgentBridgeBuilder",
    "AgentBridgeContributions",
    "BufferedCapabilityBridge",
    "CapabilityBridge",
    "ClientFilesystemBackend",
    "ClientHostContext",
    "ClientTerminalBackend",
    "ConfigOption",
    "ConfigOptionsProvider",
    "CompositeProjectionMap",
    "compose_projection_maps",
    "FileSessionStore",
    "FileSystemProjectionMap",
    "FactoryAgentSource",
    "FilesystemBackend",
    "HistoryProcessorBridge",
    "HookEvent",
    "HookProjectionMap",
    "HookBridge",
    "JsonValue",
    "list_agent_hooks",
    "MemorySessionStore",
    "McpBridge",
    "McpServerDefinition",
    "McpToolDefinition",
    "ModeState",
    "ModelSelectionState",
    "NativeApprovalBridge",
    "NativePlanPersistenceProvider",
    "PlanProvider",
    "ProjectionMap",
    "PrepareToolsBridge",
    "PrepareToolsMode",
    "RegisteredHookInfo",
    "RuntimeAgent",
    "ThinkingBridge",
    "SessionStore",
    "SessionModelsProvider",
    "SessionModesProvider",
    "StaticAgentSource",
    "TerminalBackend",
    "create_acp_agent",
    "run_acp",
)
