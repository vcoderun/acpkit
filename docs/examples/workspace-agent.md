# Workspace Agent

The maintained workspace showcase is [`examples/pydantic/strong_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/strong_agent.py).

This page is not a source listing. It highlights the patterns that matter in a real ACP-backed coding agent and explains how the example combines them.

## What This Example Is Showing

`strong_agent.py` demonstrates a full workspace integration where ACP owns the session surface, while the underlying Pydantic AI agent still looks like an ordinary `Agent(...)`.

The important parts are:

- provider-owned model and mode state
- mode-aware tool filtering
- native ACP plan persistence
- host-backed filesystem and terminal tools
- MCP metadata for repo and host tool surfaces
- approval-aware mutation flows
- projection maps so clients can render file and shell activity well

## Pattern Map

| Pattern | Where it lives in `strong_agent.py` | Why it matters |
|---|---|---|
| Mode-aware tool shaping | `_ask_tools`, `_plan_tools`, `_agent_tools`, `_build_bridges` | Keeps ACP tool surface truthful per mode |
| Session-local model state | `WorkspaceModelsProvider` | Makes model selection explicit and persisted |
| Session-local mode state | `WorkspaceModesProvider` | Drives dynamic slash commands and ACP mode UI |
| Approval metadata | `WorkspaceApprovalStateProvider` | Exposes host-relevant approval state into ACP metadata |
| Native plan persistence | `WorkspaceNativePlanPersistenceProvider` | Mirrors ACP plan state into durable storage |
| Host binding and tool registration | `WorkspaceAgentSource.get_agent` | Owns workspace root, host context, and tool setup |
| Projection maps | `_build_projection_maps` | Lets clients render file reads, writes, and shell activity well |
| Final assembly | `build_server_agent` | Keeps a complex runtime readable at composition time |

## Pattern 1: Mode-aware Tool Surfaces

The example defines three modes and uses `PrepareToolsBridge` to shape the visible tool surface for each one.

```python
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
)
```

What each mode means in practice:

### `ask`

- repo search and repo reads stay available
- host-backed reads, writes, and shell execution are hidden
- useful for explanation, inspection, and review

### `plan`

- repo inspection still works
- ACP plan generation is active
- workspace writes and shell execution are hidden
- useful for staged planning before mutation

### `agent`

- full host tool surface is visible
- plan progress tools stay available
- mutations can proceed through approval flow

The point of this split is that ACP clients see a truthful surface. The agent is not merely told “please behave read-only”; the write and shell tools are actually removed when the mode says they should be.

## Pattern 2: Providers Own Session-visible State

The example does not hardcode session state in the adapter. It uses providers so that model and mode state remain explicit and host-controlled.

```python
@dataclass(slots=True, frozen=True, kw_only=True)
class WorkspaceModelsProvider:
    def get_model_state(...) -> ModelSelectionState: ...
    def set_model(...) -> ModelSelectionState: ...


@dataclass(slots=True, frozen=True, kw_only=True)
class WorkspaceModesProvider:
    def get_mode_state(...) -> ModeState: ...
    def set_mode(...) -> ModeState: ...
```

Why this matters:

- ACP can render model selection and mode selection in the UI
- the product layer keeps control over valid values
- state is persisted in the session instead of hidden in prompts

The same pattern is used for approval metadata through `WorkspaceApprovalStateProvider`.

## Pattern 3: Native Plan State Is Persisted Outside Prompt Text

The example keeps ACP plan state as a real session artifact, not a blob of assistant markdown.

```python
@dataclass(slots=True, frozen=True, kw_only=True)
class WorkspaceNativePlanPersistenceProvider:
    def persist_plan_state(
        self,
        session: AcpSessionContext,
        agent: Agent[None, str | DeferredToolRequests],
        entries: Sequence[PlanEntry],
        plan_markdown: str | None,
    ) -> None:
        storage_path = _current_plan_storage_path(session)
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        storage_path.write_text(
            _render_plan_document(entries=entries, plan_markdown=plan_markdown),
            encoding="utf-8",
        )
```

This gives the example two useful properties:

- ACP clients can resume and render the real plan state
- the host can keep a file-backed trace of the current session plan

The agent itself is instructed to use ACP-native plan surfaces truthfully: `acp_set_plan` when the host exposes tool-based plan recording, `acp_get_plan` for plan reads, and `acp_update_plan_entry` / `acp_mark_plan_done` for progress updates.

## Pattern 4: `AgentSource` Owns Host Binding And Tool Registration

The core construction seam is `WorkspaceAgentSource.get_agent(...)`.

This is where the example:

- resolves the current workspace root from `session.cwd`
- builds bridge contributions
- binds client-backed filesystem and terminal helpers when a host is connected
- registers repo tools and host tools on the Pydantic AI agent

The repo-facing tools are plain and always local to the workspace:

```python
@agent.tool_plain(name=_SEARCH_REPO_TOOL)
def search_repo_paths(query: str) -> str:
    ...


@agent.tool_plain(name=_READ_REPO_TOOL)
def read_repo_file(path: str, max_chars: int = 4000) -> str:
    ...
```

When the session is bound to a real ACP host, the example also exposes client-backed workspace tools:

```python
@agent.tool(name=_READ_WORKSPACE_TOOL)
async def read_workspace_file(ctx: RunContext[None], path: str) -> str:
    ...


@agent.tool(name=_WRITE_WORKSPACE_TOOL, requires_approval=True)
async def write_workspace_file(
    ctx: RunContext[None],
    path: str,
    content: str,
) -> str:
    ...


@agent.tool(name=_RUN_COMMAND_TOOL, requires_approval=True)
async def run_command(
    ctx: RunContext[None],
    command: str,
) -> dict[str, str | int]:
    ...
```

That separation is important:

- repo tools are safe and deterministic
- host tools exist only when the session is actually bound to host capabilities
- approval requirements stay attached to the mutating tools themselves

## Pattern 5: Bridge Composition Is Kept Central

The example keeps bridge wiring in one place instead of scattering it across the agent constructor.

```python
def _build_bridges() -> list[CapabilityBridge]:
    return [
        HookBridge(hide_all=True),
        HistoryProcessorBridge(),
        ThinkingBridge(),
        PrepareToolsBridge(...),
        McpBridge(...),
    ]
```

Each bridge has a focused job:

- `HookBridge(hide_all=True)` keeps hook machinery available without noisy UI output
- `HistoryProcessorBridge()` exposes history processor metadata
- `ThinkingBridge()` exposes session-local reasoning effort
- `PrepareToolsBridge(...)` shapes tool visibility by mode
- `McpBridge(...)` describes which tools belong to repo vs host MCP surfaces

This is the practical bridge pattern in the SDK: each ACP-visible concern is added explicitly, and the list remains readable.

## Pattern 6: Projection Maps Improve Client Rendering

The example also defines projection maps separately:

```python
def _build_projection_maps() -> tuple[FileSystemProjectionMap, ...]:
    return (
        FileSystemProjectionMap(
            read_tool_names=frozenset({_READ_REPO_TOOL, _READ_WORKSPACE_TOOL}),
            write_tool_names=frozenset({_WRITE_WORKSPACE_TOOL}),
            bash_tool_names=frozenset({_RUN_COMMAND_TOOL}),
        ),
    )
```

This lets ACP clients render reads, writes, and shell activity as richer UI events instead of plain text.

It is a good pattern whenever the host tools are stable and you want predictable client-side rendering.

## Pattern 7: Final Server Assembly Stays Small

All of the complexity above is composed into a short `build_server_agent()` function:

```python
return create_acp_agent(
    agent_source=WorkspaceAgentSource(
        capability_bridges=capability_bridges,
    ),
    config=AdapterConfig(
        approval_bridge=NativeApprovalBridge(enable_persistent_choices=True),
        approval_state_provider=WorkspaceApprovalStateProvider(),
        capability_bridges=list(capability_bridges),
        models_provider=WorkspaceModelsProvider(),
        modes_provider=WorkspaceModesProvider(),
        native_plan_persistence_provider=WorkspaceNativePlanPersistenceProvider(),
        projection_maps=_build_projection_maps(),
        session_store=FileSessionStore(session_store_dir),
    ),
)
```

That is the main takeaway of the showcase: the runtime is rich, but the assembly stays understandable because each responsibility has a clear seam.

## When To Use This Structure

Use this pattern when you need a real coding-agent runtime rather than a chat-only demo.

It fits well when you need:

- staged `ask -> plan -> agent` behavior
- ACP-visible model and mode controls
- durable plan state
- file and shell tools that come from the connected host
- approval-aware mutation workflows
- strong mapping between docs and a maintained production-style example
