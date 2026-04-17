# `pydantic_acp` API

This page documents the public surface re-exported by `pydantic_acp`.

## Functions

::: pydantic_acp.create_acp_agent

::: pydantic_acp.run_acp

::: pydantic_acp.compose_projection_maps

## Core Classes And Data Types

::: pydantic_acp.AdapterConfig

::: pydantic_acp.AdapterModel

::: pydantic_acp.AcpSessionContext

::: pydantic_acp.JsonValue

::: pydantic_acp.RuntimeAgent

## Agent Source Classes And Protocols

::: pydantic_acp.AgentFactory

::: pydantic_acp.AgentSource

::: pydantic_acp.StaticAgentSource

::: pydantic_acp.FactoryAgentSource

## Session Store Classes

::: pydantic_acp.SessionStore

::: pydantic_acp.MemorySessionStore

::: pydantic_acp.FileSessionStore

## Provider State Classes And Protocols

::: pydantic_acp.ModelSelectionState

::: pydantic_acp.ModeState

::: pydantic_acp.SessionModelsProvider

::: pydantic_acp.SessionModesProvider

::: pydantic_acp.ConfigOptionsProvider

::: pydantic_acp.PlanProvider

::: pydantic_acp.NativePlanPersistenceProvider

::: pydantic_acp.ApprovalStateProvider

## Bridge Classes

::: pydantic_acp.CapabilityBridge

::: pydantic_acp.BufferedCapabilityBridge

::: pydantic_acp.PrepareToolsBridge

::: pydantic_acp.PrepareToolsMode

::: pydantic_acp.ThinkingBridge

::: pydantic_acp.HookBridge

::: pydantic_acp.HistoryProcessorBridge

::: pydantic_acp.ThreadExecutorBridge

::: pydantic_acp.ImageGenerationBridge

::: pydantic_acp.SetToolMetadataBridge

::: pydantic_acp.IncludeToolReturnSchemasBridge

::: pydantic_acp.ToolsetBridge

::: pydantic_acp.PrefixToolsBridge

::: pydantic_acp.WebSearchBridge

::: pydantic_acp.WebFetchBridge

::: pydantic_acp.McpCapabilityBridge

::: pydantic_acp.OpenAICompactionBridge

::: pydantic_acp.AnthropicCompactionBridge

::: pydantic_acp.McpBridge

::: pydantic_acp.McpServerDefinition

::: pydantic_acp.McpToolDefinition

## Hook Introspection Helpers

::: pydantic_acp.RegisteredHookInfo

::: pydantic_acp.list_agent_hooks

## Projection Classes

::: pydantic_acp.FileSystemProjectionMap

::: pydantic_acp.WebToolProjectionMap

::: pydantic_acp.BuiltinToolProjectionMap

::: pydantic_acp.CompositeProjectionMap

## Projection Helpers

::: pydantic_acp.truncate_text

::: pydantic_acp.truncate_lines

::: pydantic_acp.single_line_summary

::: pydantic_acp.format_code_block

::: pydantic_acp.format_diff_preview

::: pydantic_acp.format_terminal_status

::: pydantic_acp.caution_for_path

::: pydantic_acp.caution_for_command

## Host Backend Classes

::: pydantic_acp.ClientHostContext

::: pydantic_acp.ClientFilesystemBackend

::: pydantic_acp.ClientTerminalBackend

## Testing Helpers

::: pydantic_acp.BlackBoxHarness

::: pydantic_acp.RecordingACPClient
