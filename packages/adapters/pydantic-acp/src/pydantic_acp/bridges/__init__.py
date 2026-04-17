from __future__ import annotations as _annotations

from .base import BufferedCapabilityBridge, CapabilityBridge
from .capability_support import (
    AnthropicCompactionBridge,
    ImageGenerationBridge,
    IncludeToolReturnSchemasBridge,
    McpCapabilityBridge,
    OpenAICompactionBridge,
    PrefixToolsBridge,
    SetToolMetadataBridge,
    ThreadExecutorBridge,
    ToolsetBridge,
    WebFetchBridge,
    WebSearchBridge,
)
from .history_processor import (
    HistoryProcessorBridge,
    HistoryProcessorCallable,
    HistoryProcessorContextual,
    HistoryProcessorPlain,
    HistoryProcessorWithContextAsync,
    HistoryProcessorWithContextSync,
)
from .hooks import HookBridge
from .mcp import McpBridge, McpServerDefinition, McpToolDefinition
from .prepare_tools import PlanGenerationType, PrepareToolsBridge, PrepareToolsMode
from .thinking import ThinkingBridge

__all__ = (
    "BufferedCapabilityBridge",
    "CapabilityBridge",
    "AnthropicCompactionBridge",
    "HistoryProcessorCallable",
    "HistoryProcessorBridge",
    "HistoryProcessorContextual",
    "HistoryProcessorPlain",
    "HistoryProcessorWithContextAsync",
    "HistoryProcessorWithContextSync",
    "HookBridge",
    "ImageGenerationBridge",
    "IncludeToolReturnSchemasBridge",
    "McpCapabilityBridge",
    "McpBridge",
    "McpServerDefinition",
    "McpToolDefinition",
    "OpenAICompactionBridge",
    "PlanGenerationType",
    "PrefixToolsBridge",
    "PrepareToolsBridge",
    "PrepareToolsMode",
    "SetToolMetadataBridge",
    "ThreadExecutorBridge",
    "ThinkingBridge",
    "ToolsetBridge",
    "WebFetchBridge",
    "WebSearchBridge",
)
