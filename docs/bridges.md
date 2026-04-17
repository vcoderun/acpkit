# Bridges

Capability bridges are the adapter’s main extension seam for ACP-visible runtime behavior.

Use a bridge when you want to contribute:

- ACP session metadata
- config options
- modes
- MCP server classification
- buffered ACP updates
- model settings derived from session state
- Pydantic AI capabilities that should be wired into the active agent

## Base Types

| Type | Purpose |
|---|---|
| `CapabilityBridge` | synchronous or async hook point for ACP-facing state and agent contributions |
| `BufferedCapabilityBridge` | base class for bridges that emit buffered ACP update objects |

## Writing A Custom `CapabilityBridge`

Most docs show how to configure built-in bridges. If you are extending the SDK, the key thing to understand is that a bridge is just a narrow, synchronous contribution surface that the runtime polls at specific points.

Use plain `CapabilityBridge` when you only need to:

- add session metadata
- classify tools
- expose MCP transport capability flags
- expose config options or mode state
- derive model settings from session state

Use `BufferedCapabilityBridge` when the bridge also needs to emit ACP transcript updates over time.

### Override Matrix

| Method | Override it when | Return value |
|---|---|---|
| `build_agent_capabilities(...)` | your bridge contributes Pydantic AI capabilities that should be attached to the active agent | `tuple[AbstractCapability, ...]` |
| `get_session_metadata(...)` | you want a metadata section under your bridge `metadata_key` | `dict[str, JsonValue]` |
| `get_tool_kind(...)` | you want custom ACP tool classification | `ToolKind` |
| `get_mcp_capabilities(...)` | your bridge requires MCP transport capability flags | `McpCapabilities` |
| `get_config_options(...)` / `set_config_option(...)` | the bridge owns ACP config surface | `list[ConfigOption]` |
| `get_mode_state(...)` / `set_mode(...)` | the bridge owns ACP-visible mode state | `ModeState` |
| `get_model_settings(...)` | session state should change model settings | `ModelSettings` |
| `drain_updates(...)` | the bridge emits buffered ACP transcript updates | `list[SessionTranscriptUpdate]` |

Practical rules:

- use `build_agent_capabilities(...)` when the bridge needs to materialize upstream Pydantic AI capabilities
- `AgentBridgeBuilder` is the adapter-local helper that turns those bridge contributions into agent constructor inputs
- set `metadata_key` if you want your metadata to appear in session metadata
- keep classification deterministic; the first bridge that returns a `ToolKind` wins
- return `None` when your bridge is not authoritative for that surface
- use bridge-local buffering only when you truly need ACP transcript updates, not just metadata

## `AgentBridgeBuilder` Is The Capability Wiring Seam

`AdapterConfig(capability_bridges=[...])` makes the adapter aware of bridge-owned ACP surfaces such as:

- session metadata
- tool classification
- config options
- mode state
- model settings

If a bridge also contributes upstream Pydantic AI capabilities, those still need to be attached to
the active agent instance.

Use `AgentBridgeBuilder(...)` inside your factory or source:

```python
builder = AgentBridgeBuilder(
    session=session,
    capability_bridges=bridges,
)
contributions = builder.build()

agent = Agent(
    model,
    capabilities=contributions.capabilities,
    history_processors=contributions.history_processors,
)
```

That is the intended seam for:

- `HookBridge`
- `PrepareToolsBridge`
- `ThreadExecutorBridge`
- `SetToolMetadataBridge`
- `IncludeToolReturnSchemasBridge`
- `WebSearchBridge`
- `WebFetchBridge`

## Compatibility Note: History Processor Types

`HistoryProcessorBridge` depends on Pydantic AI history-processor callable types.
ACP Kit models those callable shapes locally and passes them through the public
`Agent(..., history_processors=...)` interface.

That means:

- bridge extension code should import history-processor aliases from
  `pydantic_acp`, not from `pydantic_ai._history_processor`
- the adapter is no longer directly coupled to upstream private
  history-processor imports

## Example: Custom Hook Introspection + MCP Metadata Classification

This is the missing pattern most custom integrations need: inspect hooks already attached to the source agent, expose them in ACP metadata, and classify a subset of tools as MCP-backed search or execute tools.

```python
from dataclasses import dataclass

from acp.schema import McpCapabilities, ToolKind
from pydantic_ai import Agent
from pydantic_acp import (
    AcpSessionContext,
    AdapterConfig,
    CapabilityBridge,
    JsonValue,
    RegisteredHookInfo,
    RuntimeAgent,
    list_agent_hooks,
    run_acp,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class HookAwareMcpBridge(CapabilityBridge):
    metadata_key: str | None = "workspace"
    search_prefix: str = "mcp_repo_"
    execute_prefix: str = "mcp_shell_"

    def get_session_metadata(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> dict[str, JsonValue]:
        hook_infos = list_agent_hooks(agent)
        return {
            "cwd": str(session.cwd),
            "hook_count": len(hook_infos),
            "hooks": [self._serialize_hook_info(hook_info) for hook_info in hook_infos],
        }

    def get_mcp_capabilities(self, agent: RuntimeAgent | None = None) -> McpCapabilities:
        del agent
        return McpCapabilities(http=True)

    def get_tool_kind(self, tool_name: str, raw_input: JsonValue | None = None) -> ToolKind | None:
        del raw_input
        if tool_name.startswith(self.search_prefix):
            return "search"
        if tool_name.startswith(self.execute_prefix):
            return "execute"
        return None

    def _serialize_hook_info(self, hook_info: RegisteredHookInfo) -> JsonValue:
        return {
            "event_id": hook_info.event_id,
            "hook_name": hook_info.hook_name,
            "tool_filters": list(hook_info.tool_filters),
        }


agent = Agent("openai:gpt-5", name="hook-aware-agent")

run_acp(
    agent=agent,
    config=AdapterConfig(
        capability_bridges=[HookAwareMcpBridge()],
    ),
)
```

What this bridge is doing:

- `list_agent_hooks(agent)` introspects hooks that were already attached to the source agent
- `metadata_key = "workspace"` makes the returned metadata appear under `session.metadata["workspace"]`
- `get_mcp_capabilities(...)` advertises that the bridge contributes MCP-aware HTTP metadata
- `get_tool_kind(...)` classifies matching tools before the base tool classifier runs

When to promote this to `BufferedCapabilityBridge`:

- you want ACP transcript cards when the bridge itself completes work
- you need `_record_completed_event(...)` or `_record_failed_event(...)`
- metadata alone is not enough; the client should see a time-ordered update stream

## Existing Hook Introspection Helpers

The bridge example above depends on one public helper:

- `list_agent_hooks(agent)`

Use it when you want to inspect hooks that already exist on the source agent.

That is different from `HookBridge`:

- **existing hook introspection**
  inspects hooks that are already present on the source agent
- **`HookBridge`**
  contributes bridge-owned hook capability at build time

If you want to render existing hook callbacks in session metadata or ACP listings, start with `list_agent_hooks(...)`.
If you want the bridge layer itself to contribute hook behavior, use `HookBridge`.

## Event Stream Hook Contract

Pydantic AI treats `run_event_stream` differently from the ordinary async hook callbacks.

The contract is:

- `run_event_stream` must return an `AsyncIterable[AgentStreamEvent]`
- it must not return a coroutine that later resolves to a stream
- if you instrument or wrap that hook, preserve the async-iterable boundary

This matters for both custom `Hooks(...)` usage and hook introspection wrappers.
If you accidentally return a coroutine, the run will fail when the runtime reaches `async for`.

## Built-in Bridges

### `PrepareToolsBridge`

Shapes tool availability per mode.

Use it for:

- read-only vs write-enabled modes
- hiding dangerous tools in planning mode
- activating native ACP plan state
- exposing plan progress tools only in execution modes

It is the bridge most real coding-agent setups start with.

### `ThinkingBridge`

Exposes Pydantic AI’s `Thinking` capability through ACP session config.

Use it when:

- you want a session-local reasoning effort selector
- ACP clients should be able to inspect or change thinking effort

### `HookBridge`

Adds a `Hooks` capability into the active agent.

Useful when you want ACP-visible hook updates that come from bridge-owned hooks rather than only from hooks already attached to the source agent.

You can also suppress noisy default hook rendering with:

```python
HookBridge(hide_all=True)
```

### `HistoryProcessorBridge`

Wraps history processors so their activity can be reflected into ACP updates.

### `WebSearchBridge`

Adds Pydantic AI's `WebSearch` capability into the active agent and classifies matching tools as ACP `search`.

Use it when:

- the runtime should expose builtin or local web search through one bridge-owned capability
- ACP transcript cards should classify `web_search` or local fallback search tools as search operations
- session metadata should show search configuration such as allowed domains or context size

Default classified tool names:

- `web_search`
- `duckduckgo_search`
- `exa_search`
- `tavily_search`

UI note:

- add `WebToolProjectionMap()` or `BuiltinToolProjectionMap()` when you want ACP transcript cards to show query/domain context at start and search results at completion instead of generic tool output

### `WebFetchBridge`

Adds Pydantic AI's `WebFetch` capability into the active agent and classifies matching tools as ACP `fetch`.

Use it when:

- the runtime should expose builtin or local URL fetching through one bridge-owned capability
- ACP transcript cards should classify `web_fetch` as a fetch operation instead of generic execute
- session metadata should show fetch guardrails such as allowed domains, citations, or token limits

This is useful when you want message-history trimming or contextual rewriting to remain observable.

UI note:

- add `WebToolProjectionMap()` or `BuiltinToolProjectionMap()` when you want ACP transcript cards to show fetched URLs, page titles, text previews, and binary-fetch status

### `ImageGenerationBridge`

Adds upstream `ImageGeneration` through the bridge-builder seam.

Use it when:

- the runtime should expose builtin image generation or a local fallback subagent through one ACP-owned seam
- session metadata should reflect image-generation policy such as quality, size, or output format
- projection maps should recognize `image_generation` or `generate_image` as intentional builtin work instead of generic tool noise

UI note:

- add `BuiltinToolProjectionMap()` when you want ACP transcript cards to show prompt, quality, size, and revised prompt summary for builtin image generation

### `ThreadExecutorBridge`

Adds Pydantic AI's `ThreadExecutor` capability through the bridge-builder seam.

Use it when:

- your ACP service is long-lived
- sync tools or callbacks should run on a bounded executor
- you want bridge-owned agent construction to keep executor policy explicit

### `SetToolMetadataBridge`

Adds upstream `SetToolMetadata` capability through the bridge-builder seam.

Use it when:

- tool metadata should be attached centrally instead of per-tool definition
- downstream selectors, MCP logic, or provider behavior depend on consistent metadata

### `IncludeToolReturnSchemasBridge`

Adds upstream `IncludeToolReturnSchemas` capability through the bridge-builder seam.

Use it when:

- you want richer tool return contracts sent to models
- downstream integrations should enable return-schema support consistently across selected tools

### `ToolsetBridge`

Adds upstream `Toolset` capability through the bridge-builder seam.

Use it when:

- a maintained `FunctionToolset` or other agent toolset should be injected through the same ACP-owned bridge path as other capabilities
- integration code wants one explicit place to wire toolset-owned instructions or wrappers

Compatibility notes:

- toolset `get_instructions()` output passes through to the upstream model request as `instruction_parts`
- ordering is explicit: `AgentBridgeBuilder.build(capabilities=...)` keeps user-supplied capabilities first, then appends bridge capabilities in configured bridge order

### `PrefixToolsBridge`

Adds upstream `PrefixTools` capability through the bridge-builder seam.

Use it when:

- a wrapped capability's tool names need a stable namespace prefix
- downstream clients should see prefixed tool names without custom tool re-registration logic

### `McpCapabilityBridge`

Adds upstream `MCP` capability through the bridge-builder seam.

Use it when:

- the model should use builtin MCP server support when available and local HTTP fallback otherwise
- ACP session metadata should expose the connected MCP URL, resolved server id, or allowlist shape
- projection maps should summarize `mcp_server:*` builtin tool calls

Compatibility note:

- this bridge is separate from MCP toolsets such as `MCPServerStdio` or `MCPServerStreamableHTTP`
- if those toolsets are attached directly to the agent with `include_instructions=True`, their server instructions still flow through the normal upstream toolset path into `instruction_parts`

UI note:

- add `BuiltinToolProjectionMap()` when you want ACP transcript cards to summarize builtin MCP calls such as `call_tool`, `list_tools`, and output previews instead of generic execute cards

### `OpenAICompactionBridge`

Adds provider-owned OpenAI Responses compaction through the bridge-builder seam.

Use it when:

- long-running ACP sessions should compact history without looking stalled in the client
- the ACP transcript should show a visible `Context Compaction` card before and after OpenAI compaction runs
- session metadata should still expose the configured trigger and instructions

UI behavior:

- no extra projection map is required
- when compaction triggers, ACP emits a visible `Context Compaction` start/update pair
- OpenAI shows provider status and round-trip payload preservation instead of a blank wait
- OpenAI completion is emitted by the bridge-owned wrapper so the same card opens and closes around the compaction request

### `AnthropicCompactionBridge`

Adds provider-owned Anthropic context-management compaction through the bridge-builder seam.

Use it when:

- Anthropic context management should be configured through the bridge seam
- Anthropic compaction summaries should be visible in ACP transcripts instead of disappearing into raw provider behavior

UI behavior:

- no extra projection map is required
- when Anthropic returns a `CompactionPart`, ACP emits a visible `Context Compaction` card
- readable Anthropic compaction summaries are shown in the completion update

## Builtin Capability Projection

Use `BuiltinToolProjectionMap()` when the agent exposes upstream builtin capability tools and you want ACP-visible cards instead of generic execute noise.

Current builtin projection coverage:

- web search
- web fetch
- image generation
- builtin MCP server tools

Compaction visibility is built into the runtime path and does not require a projection map.

### `McpBridge`

Adds MCP-aware metadata and tool classification:

- server definitions
- tool-to-server mapping
- tool kind classification
- approval-policy key routing
- optional config surface for MCP-backed state

## Example: Mode-aware Tools + MCP Metadata + Thinking

```python
from pydantic_acp import (
    AdapterConfig,
    McpBridge,
    McpServerDefinition,
    McpToolDefinition,
    PrepareToolsBridge,
    PrepareToolsMode,
    ThinkingBridge,
)
from pydantic_ai.tools import RunContext, ToolDefinition


def ask_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return [tool_def for tool_def in tool_defs if tool_def.name == "mcp_repo_search_paths"]


def agent_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return list(tool_defs)


config = AdapterConfig(
    capability_bridges=[
        ThinkingBridge(),
        PrepareToolsBridge(
            default_mode_id="ask",
            modes=[
                PrepareToolsMode(
                    id="ask",
                    name="Ask",
                    description="Read-only inspection mode.",
                    prepare_func=ask_tools,
                ),
                PrepareToolsMode(
                    id="agent",
                    name="Agent",
                    description="Full workspace mode.",
                    prepare_func=agent_tools,
                    plan_tools=True,
                ),
            ],
        ),
        McpBridge(
            servers=[
                McpServerDefinition(
                    server_id="repo",
                    name="Repository",
                    transport="http",
                    tool_prefix="mcp_repo_",
                    description="Repository inspection tools.",
                )
            ],
            tools=[
                McpToolDefinition(
                    tool_name="mcp_repo_search_paths",
                    server_id="repo",
                    kind="search",
                )
            ],
        ),
    ],
)
```

## Bridge Builder

`AgentBridgeBuilder` is the intended way to assemble bridge contributions into a session-specific agent build:

```python
from pydantic_acp import AgentBridgeBuilder

builder = AgentBridgeBuilder(
    session=session,
    capability_bridges=bridges,
)
contributions = builder.build()
```

It returns:

- `capabilities`
- `history_processors`

That makes it a natural fit inside `agent_factory` or `AgentSource.get_agent(...)`.

## Common Failure Modes

- defining multiple `PrepareToolsMode(..., plan_mode=True)` entries raises an error; native plan mode is singular
- using reserved mode ids such as `model`, `thinking`, `tools`, `hooks`, or `mcp-servers` raises an error because those names are reserved for slash commands
- `HookBridge(hide_all=True)` hides hook listing output; it does not remove hook capability wiring
- `run_event_stream` wrappers must return an async iterable; returning a coroutine or plain object breaks stream execution
- `McpBridge` only contributes MCP metadata and classification; it does not register the underlying tools for you

## Existing Hook Introspection vs HookBridge

These are related but not identical:

- **existing hook introspection**
  observes a `Hooks` capability that was already present on the source agent
- **`HookBridge`**
  contributes a bridge-owned `Hooks` capability during the session build

If you want to render existing hook callbacks, use `HookProjectionMap`.
If you want the bridge layer to contribute hooks itself, use `HookBridge`.
