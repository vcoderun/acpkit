---
name: acpkit-sdk
description: Use for ACP Kit SDK tasks that turn an existing agent surface into a truthful ACP server through acpkit, pydantic-acp, the published docs, and the maintained examples.
---

# ACP Kit SDK

ACP Kit is the adapter toolkit and monorepo for turning an existing agent surface into a truthful ACP server boundary.

Today the stable production focus is `pydantic-acp`: exposing `pydantic_ai.Agent` through ACP
while keeping models, modes, plans, approvals, MCP metadata, host tools, and session state
aligned with what the underlying runtime can actually support.

Additional adapters such as `langchain-acp` and `dspy-acp` are planned after `pydantic-acp`
reaches 1.0 stability.

This skill file is the longform, high-context entrypoint for the packaged `acpkit-sdk` skill and
should be treated as the primary skill surface when the skill is selected.

When you need the docs map or the full docs corpus in one place, read [llms.txt](https://vcoderun.github.io/acpkit/llms.txt) or [llms-full.txt](https://vcoderun.github.io/acpkit/llms-full.txt).

## What ACP Kit Ships

ACP Kit currently has three main Python packages:

| Package | Role | Typical use |
| --- | --- | --- |
| `acpkit` | root CLI and target resolver | `acpkit run ...`, `acpkit launch ...`, target loading |
| `pydantic-acp` | ACP adapter for `pydantic_ai.Agent` | expose an existing agent through ACP without rewriting it |
| `codex-auth-helper` | Codex-backed Pydantic AI helper | build Codex-backed Responses models from a local Codex login |

The core contract across the repo is:

> expose ACP state only when the underlying runtime can actually honor it.

That rule affects model selection, mode switching, slash commands, native plan state, approval
flow, MCP metadata, hook rendering, cancellation, and host-backed tooling.

## Use The Right Construction Seam

Pick the narrowest seam that matches the job:

| Seam | Use it when |
| --- | --- |
| `run_acp(agent=...)` | you want the smallest direct path from `pydantic_ai.Agent` to a running ACP server |
| `create_acp_agent(...)` | you need the ACP-compatible agent object before running it |
| `agent_factory=` | the current session should influence agent construction, but a full custom source is unnecessary |
| `agent_source=` | you need full control over the agent build path, host binding, and session-specific dependencies |
| built-in `AdapterConfig` fields | the adapter can own the relevant runtime state cleanly |
| providers | the host or product layer should remain the source of truth |
| bridges | the runtime needs ACP-visible capabilities without hard-coding them into the adapter core |

## CLI And Target Resolution

The root `acpkit` package resolves Python targets and dispatches them to the matching adapter.

```bash
acpkit run my_agent
acpkit run my_agent:agent
acpkit run app.agents.demo:agent -p ./examples
acpkit launch my_agent:agent -p ./examples
acpkit launch --command "python3.11 strong_agent.py"
```

Target resolution behavior:

1. add the current working directory to `sys.path`
2. add any `-p/--path` roots
3. import the requested module
4. if `module:attribute` was given, resolve the attribute path
5. if only `module` was given, select the last defined `pydantic_ai.Agent` in that module

Current built-in auto-dispatch support is centered on `pydantic_ai.Agent`.

## Smallest Adapter Path: `run_acp(...)`

Use `run_acp(...)` when one existing agent instance is enough.

```python
from pydantic_ai import Agent
from pydantic_acp import run_acp

agent = Agent(
    'openai:gpt-5',
    name='weather-agent',
    instructions='Answer briefly and ask for clarification when location is missing.',
)


@agent.tool_plain
def lookup_weather(city: str) -> str:
    """Return a canned weather response for demos."""

    return f'Weather in {city}: sunny'


run_acp(agent=agent)
```

This is the narrowest path from an existing agent surface to a live ACP server.

## ACP Agent Without Running: `create_acp_agent(...)`

Use `create_acp_agent(...)` when you need the ACP-compatible agent object first.

```python
from acp import run_agent
from pydantic_ai import Agent
from pydantic_acp import AdapterConfig, MemorySessionStore, create_acp_agent

agent = Agent('openai:gpt-5', name='composable-agent')

acp_agent = create_acp_agent(
    agent=agent,
    config=AdapterConfig(
        agent_name='my-service',
        agent_title='My Service Agent',
        session_store=MemorySessionStore(),
    ),
)

# later:
# await run_agent(acp_agent)
```

Use this seam when ACP is only one part of a larger async service boundary.

## `AdapterConfig` Is The Main Runtime Surface

`AdapterConfig` is where the adapter’s built-in ownership lives:

- session storage
- model selection
- approval bridging
- capability bridges
- plan persistence callbacks
- hook projection and runtime shaping

```python
from pathlib import Path

from pydantic_ai import Agent
from pydantic_acp import (
    AdapterConfig,
    AdapterModel,
    FileSessionStore,
    NativeApprovalBridge,
    ThinkingBridge,
    run_acp,
)

agent = Agent('openai:gpt-5', name='configured-agent')

config = AdapterConfig(
    agent_name='my-agent',
    agent_title='My Agent Title',
    allow_model_selection=True,
    available_models=[
        AdapterModel(
            model_id='fast',
            name='Fast',
            description='Lower-latency responses.',
            override='openai:gpt-5-mini',
        ),
        AdapterModel(
            model_id='smart',
            name='Smart',
            description='Higher-quality responses.',
            override='openai:gpt-5',
        ),
    ],
    capability_bridges=[ThinkingBridge()],
    approval_bridge=NativeApprovalBridge(enable_persistent_choices=True),
    session_store=FileSessionStore(root=Path('.acp-sessions')),
)

run_acp(agent=agent, config=config)
```

High-value detail:

- `FileSessionStore` takes `root=Path(...)`, not `base_dir=...`

## Session Stores

ACP Kit currently ships two session stores:

- `MemorySessionStore`
- `FileSessionStore`

`MemorySessionStore` is ephemeral. `FileSessionStore` persists ACP sessions across restarts and
supports:

- save
- get
- list
- fork
- delete

Use `FileSessionStore(root=Path(...))` when real ACP clients need durable session state.

Current `FileSessionStore` behavior is tuned for durable local-host ACP use:

- atomic temp-file write plus replace
- local process lock and filesystem advisory lock when available
- malformed saved session files are skipped in public load/list flows
- stale temp files are cleaned up on startup

It is a strong default for editors and single-host services, not a distributed shared-state backend.

## Agent Factories And `AgentSource`

Use an agent factory when session context changes agent construction but you do not need a full
custom source object.

```python
from pydantic_ai import Agent
from pydantic_acp import AcpSessionContext, AdapterConfig, run_acp


def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    workspace_name = session.cwd.name
    return Agent(
        'openai:gpt-5',
        name=f'agent-{workspace_name}',
        instructions=f'You are working in {workspace_name}.',
    )


run_acp(
    agent_factory=build_agent,
    config=AdapterConfig(agent_name='factory-agent'),
)
```

Use `AgentSource` when you need full control over both the agent and the dependency object:

```python
from dataclasses import dataclass

from pydantic_ai import Agent
from pydantic_acp import AcpSessionContext, AgentSource, AdapterConfig, run_acp


@dataclass(frozen=True, slots=True)
class Deps:
    workspace_name: str


class WorkspaceSource(AgentSource[Deps]):
    async def get_agent(self, session: AcpSessionContext) -> Agent[Deps, str]:
        return Agent(
            'openai:gpt-5',
            name='workspace-agent',
            instructions='Use the provided workspace dependencies.',
        )

    async def get_deps(self, session: AcpSessionContext) -> Deps:
        return Deps(workspace_name=session.cwd.name)


run_acp(
    agent_source=WorkspaceSource(),
    config=AdapterConfig(agent_name='workspace-agent'),
)
```

Reach for `AgentSource` in production-style examples where:

- session cwd matters
- model ids or modes come from the host
- tools depend on host-backed services
- you need a richer dependency object than the adapter can infer on its own

## Models, Modes, And Slash Commands

ACP Kit can expose session-local model and mode switching through two different ownership paths.

### Built-in model selection

Use built-in model selection when the adapter can own the available model set:

```python
from pydantic_acp import AdapterConfig, AdapterModel

config = AdapterConfig(
    allow_model_selection=True,
    available_models=[
        AdapterModel(
            model_id='fast',
            name='Fast',
            description='Lower latency.',
            override='openai:gpt-5-mini',
        ),
        AdapterModel(
            model_id='smart',
            name='Smart',
            description='Higher quality.',
            override='openai:gpt-5',
        ),
    ],
)
```

### Provider-owned model or mode state

Use providers when the host already owns that state:

- `SessionModelsProvider`
- `SessionModesProvider`
- `ConfigOptionsProvider`

Mode slash commands are dynamic. They are derived from the configured mode ids rather than from a
hard-coded global set.

Important guardrail:

- mode ids must not collide with reserved slash-command names such as `model`, `thinking`,
  `tools`, `hooks`, or `mcp-servers`

## Capability Bridges

Capability bridges are how ACP-visible runtime behavior gets added without hard-coding everything
into the adapter core.

Common bridges:

- `PrepareToolsBridge`
- `ThinkingBridge`
- `McpBridge`
- `HookBridge`
- `HistoryProcessorBridge`

Use `PrepareToolsBridge` to define dynamic modes and tool surfaces:

```python
from pydantic_acp import PrepareToolsBridge, PrepareToolsMode
from pydantic_ai.tools import RunContext, ToolDefinition


def ask_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return [tool_def for tool_def in tool_defs if not tool_def.name.startswith('write_')]


def agent_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return list(tool_defs)


bridge = PrepareToolsBridge(
    default_mode_id='ask',
    modes=[
        PrepareToolsMode(
            id='ask',
            name='Ask',
            description='Read-only inspection mode.',
            prepare_func=ask_tools,
        ),
        PrepareToolsMode(
            id='plan',
            name='Plan',
            description='Native ACP plan mode.',
            prepare_func=ask_tools,
            plan_mode=True,
        ),
        PrepareToolsMode(
            id='agent',
            name='Agent',
            description='Full tool surface.',
            prepare_func=agent_tools,
            plan_tools=True,
        ),
    ],
)
```

High-value guardrails:

- only one `PrepareToolsMode(..., plan_mode=True)` is allowed
- `plan_tools=True` is how a non-plan execution mode keeps plan progress tools visible
- `ThinkingBridge()` is what makes `/thinking` and the ACP-visible effort selector exist
- `HookBridge(hide_all=True)` suppresses hook listing output without removing the hook seam itself
- custom `run_event_stream` hooks or wrappers must return an async iterable; returning a coroutine will break stream execution

## Plans, Approvals, Cancellation, And Host Tools

Native ACP plan state is separate from provider-owned plan state.

Use native plan state when you want the adapter to own the ACP-visible plan lifecycle. Use
`PlanProvider` when the host should remain the source of truth.

Current plan behavior to remember:

- `plan_mode=True` exposes native ACP plan creation tools
- `plan_tools=True` lets execution modes keep plan progress tools visible
- native plan persistence can be mirrored outward through `NativePlanPersistenceProvider`

Approval behavior:

- `NativeApprovalBridge` powers the live ACP approval flow
- `ApprovalStateProvider` exposes extra approval metadata when the host already owns approval state

Cancellation behavior:

- ACP cancellation is wired through the runtime now
- cancellation preserves session state and transcript integrity instead of leaving the session in a broken partial state

Host-backed tools:

- ACP Kit can expose client-backed filesystem and shell helpers
- projection maps change how tools are rendered in ACP clients without changing the underlying tool contract

### HostAccessPolicy

`HostAccessPolicy` is ACP Kit's native typed guardrail surface for host-backed filesystem and terminal access.

Reach for it first when an integration has started inventing ad hoc rules such as:

- "warn for absolute paths but deny workspace escapes"
- "show one caution in the client but enforce a different rule in the backend"
- "treat command cwd and file paths as unrelated policy domains"

Use it:

- when host-backed file and terminal tools already exist
- when approvals or projection warnings need to describe the same risk model that enforcement uses
- when downstream code has started to accumulate one-off path checks

Do not reach for it:

- when the integration does not expose host-backed file or terminal tools at all
- when the problem is product-specific approval wording rather than reusable access policy

Use it when an integration needs one reusable place to decide:

- whether absolute paths should only warn or hard fail
- whether paths outside the active session cwd should warn or deny
- whether workspace-root escapes should always deny
- whether command cwd and command path arguments should follow the same policy language as file paths

Important distinction:

- `evaluate_path(...)` and `evaluate_command(...)` are for UI and approval surfaces
- `enforce_path(...)` and `enforce_command(...)` are for actual blocking before ACP host requests are sent

The evaluation objects are intentionally UI-friendly. They expose:

- `disposition`
- `headline`
- `message`
- `recommendation`
- `risks`
- `risk_codes`
- `primary_risk`
- `summary_lines()`

That means downstream integrations do not need to invent their own warning strings just to show a clear caution card.

Typical use:

```python
from pydantic_acp import ClientHostContext, HostAccessPolicy

policy = HostAccessPolicy.strict()

host = ClientHostContext.from_session(
    client=client,
    session=session,
    access_policy=policy,
    workspace_root=session.cwd,
)
```

Small verified evaluation example:

```python
from pathlib import Path

from pydantic_acp import HostAccessPolicy

policy = HostAccessPolicy.strict()
evaluation = policy.evaluate_path(
    '../notes.txt',
    session_cwd=Path('/workspace/app'),
    workspace_root=Path('/workspace/app'),
)

assert evaluation.disposition == 'deny'
assert evaluation.should_deny
assert 'outside_cwd' in evaluation.risk_codes
```

Current presets:

- `HostAccessPolicy()` is conservative default behavior
- `HostAccessPolicy.strict()` denies more aggressively outside the active cwd
- `HostAccessPolicy.permissive()` keeps more paths executable but still surfaces risk

Current scope:

- file path evaluation
- command cwd evaluation
- heuristic detection of obvious path-like command arguments
- native backend-side deny enforcement

Current limit:

- it is not a full shell parser
- it does not automatically wire itself through every integration seam yet

Primary references:

- [Host Backends and Projections](https://vcoderun.github.io/acpkit/host-backends/)
- [Projection Cookbook](https://vcoderun.github.io/acpkit/projection-cookbook/)

## Black-box Integration Harness

`BlackBoxHarness` exists so downstream integrations can prove the ACP boundary without rebuilding test plumbing from scratch.

Reach for it when the integration already "works" but still lacks proof for:

- approval replay
- host-backed side effects
- session reload correctness
- ACP-visible transcript truthfulness

Use it:

- after the integration already has a real adapter construction seam
- when you need one reusable way to drive approvals, prompts, reloads, and visible updates
- when a normal unit test would miss ACP-visible behavior

Do not use it:

- to inspect private helper ordering
- as a substitute for product-level end-to-end testing
- before the integration has a coherent ownership model for sessions, approvals, and host tools

Use it when you want to verify:

- session create/load behavior
- visible ACP updates
- approval roundtrips
- host-backed file or terminal flows
- replay after reload

What it gives you:

- adapter construction plus a recording ACP client in one object
- `new_session(...)`
- `load_session(...)`
- `prompt_text(...)`
- `set_mode(...)`
- `set_model(...)`
- permission response queueing helpers
- update filtering
- reconstructed agent messages

Typical use:

```python
import asyncio

from pydantic_acp import AdapterConfig, BlackBoxHarness, FileSessionStore

harness = BlackBoxHarness.create(
    agent_factory=build_agent,
    config=AdapterConfig(session_store=FileSessionStore(tmp_path / 'sessions')),
)

session = asyncio.run(harness.new_session(cwd=str(tmp_path)))
harness.queue_permission_selected('allow_once')
response = asyncio.run(harness.prompt_text('Write the workspace note.'))

assert response.stop_reason == 'end_turn'
assert harness.tool_updates(session_id=session.session_id)
assert harness.agent_messages(session_id=session.session_id)
```

Small verified example from the harness test shape:

```python
session = asyncio.run(harness.new_session(cwd=str(tmp_path)))
harness.queue_permission_selected('allow_once')
response = asyncio.run(harness.prompt_text('Write the workspace note.'))

assert response.stop_reason == 'end_turn'
assert harness.agent_messages(session_id=session.session_id) == ['done']
```

The harness is intentionally black-box.

Prefer asserting on:

- ACP return values
- emitted `ToolCallStart` / `ToolCallProgress` updates
- reconstructed visible messages
- persisted replay behavior
- real host-backed side effects

Do not use it to:

- inspect private helper choreography
- lock internal runtime call order
- replace product-level end-to-end tests

Good default scenario ladder for a new integration:

1. session create -> prompt -> reload
2. approval required -> allow once
3. approval required -> deny once
4. host-backed file read/write
5. host-backed terminal execution
6. mode switch changes behavior
7. model switch changes session-local state

Primary references:

- [Integration Testing](https://vcoderun.github.io/acpkit/integration-testing/)
- [Examples Overview](https://vcoderun.github.io/acpkit/examples/)

## Projection Maps And Hook Rendering

Projection maps make ACP clients see richer file or command behavior than a raw generic tool card.

Common maps:

- filesystem maps
- bash / command maps
- hook projection maps

Use them when the client should see more structured output, but avoid pretending a tool is
something it is not.

### Projection Helper Primitives

ACP Kit now also ships small reusable projection helpers for integrations that need consistent shaping but do not want to rebuild truncation and warning logic repeatedly.

High-value helpers:

- `truncate_text(...)`
- `truncate_lines(...)`
- `single_line_summary(...)`
- `format_code_block(...)`
- `format_diff_preview(...)`
- `format_terminal_status(...)`
- `caution_for_path(...)`
- `caution_for_command(...)`

Use these helpers when:

- a chat client needs a plain-text diff preview
- command titles should stay compact and consistent
- long stdout/stderr content needs predictable truncation
- caution text should come from the same `HostAccessPolicy` evaluation model as backend enforcement

These helpers are intentionally small. They are building blocks, not a full rendering framework.

## Examples That Matter

High-value maintained examples live under `examples/pydantic/`:

| Example | Purpose |
| --- | --- |
| [`examples/pydantic/acp_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/acp_agent.py) | smallest direct `run_acp(...)` path |
| [`examples/pydantic/factory_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/factory_agent.py) | session-aware factory |
| [`examples/pydantic/providers.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/providers.py) | host-owned models, modes, config, plan, and approval metadata |
| [`examples/pydantic/approvals.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/approvals.py) | deferred approval flow |
| [`examples/pydantic/bridges.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/bridges.py) | bridge builder and ACP-visible capabilities |
| [`examples/pydantic/host_context.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/host_context.py) | client-backed filesystem and terminal helpers |
| [`examples/pydantic/hook_projection.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/hook_projection.py) | hook rendering and `HookProjectionMap` behavior |
| [`examples/pydantic/strong_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/strong_agent.py) | production-style workspace coding-agent showcase |
| [`examples/pydantic/strong_agent_v2.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/strong_agent_v2.py) | alternative workspace integration shape |

Use [`strong_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/strong_agent.py) when the docs or code need to highlight:

- mode-aware tool shaping
- provider-owned model and mode state
- native plan persistence
- host-backed tools
- MCP metadata mapping
- bridge composition
- projection maps
- final `create_acp_agent(...)` assembly

## Documentation Sources

High-value docs pages:

- [ACP Kit Overview](https://vcoderun.github.io/acpkit/)
- [Pydantic ACP Overview](https://vcoderun.github.io/acpkit/pydantic-acp/)
- [AdapterConfig](https://vcoderun.github.io/acpkit/pydantic-acp/adapter-config/)
- [Session State and Lifecycle](https://vcoderun.github.io/acpkit/pydantic-acp/session-state/)
- [Models, Modes, and Slash Commands](https://vcoderun.github.io/acpkit/pydantic-acp/runtime-controls/)
- [Plans, Thinking, and Approvals](https://vcoderun.github.io/acpkit/pydantic-acp/plans-thinking-approvals/)
- [Providers](https://vcoderun.github.io/acpkit/providers/)
- [Bridges](https://vcoderun.github.io/acpkit/bridges/)
- [Host Backends and Projections](https://vcoderun.github.io/acpkit/host-backends/)
- [Integration Testing](https://vcoderun.github.io/acpkit/integration-testing/)
- [Projection Cookbook](https://vcoderun.github.io/acpkit/projection-cookbook/)
- [Examples Overview](https://vcoderun.github.io/acpkit/examples/)
- [Workspace Agent](https://vcoderun.github.io/acpkit/examples/workspace-agent/)
- [pydantic_acp API](https://vcoderun.github.io/acpkit/api/pydantic_acp/)

## Compatibility Manifest

ACP Kit now also ships a typed root-level compatibility manifest schema through `acpkit`.

Reach for it after the integration already has real seams and at least one black-box proof path.

Do not use it as a speculative roadmap scratchpad. Use it as a reviewable declaration of what is actually wired today.

Use it:

- after at least one black-box proof path exists
- when reviews need one typed declaration of supported ACP surfaces
- when docs should be generated from validated code instead of prose drift

Do not use it:

- before the integration can already demonstrate the behavior it claims
- as a replacement for proof tests
- as a vague backlog matrix with no mapping seam

Use it when a real integration needs one reviewable declaration of:

- which ACP surfaces are implemented
- which are partial
- which are intentionally not used
- which are only planned

Core types:

- `CompatibilityManifest`
- `SurfaceSupport`
- `SurfaceStatus`
- `SurfaceOwner`

Typical use:

```python
from acpkit import CompatibilityManifest, SurfaceSupport

manifest = CompatibilityManifest(
    integration_name='workspace-agent',
    adapter='pydantic-acp',
    surfaces={
        'session.load': SurfaceSupport(
            status='implemented',
            owner='adapter',
            mapping='FileSessionStore + load_session',
        ),
        'mode.switch': SurfaceSupport(
            status='partial',
            owner='bridge',
            mapping='PrepareToolsBridge dynamic modes',
            rationale='Only explicitly exposed runtime modes are surfaced.',
        ),
        'authenticate': SurfaceSupport(
            status='planned',
            rationale='No auth handshake has been added yet.',
        ),
    },
)

manifest.validate()
```

Minimal review rule:

- every `implemented` surface should point to one concrete mapping seam
- every `partial`, `intentionally_not_used`, and `planned` surface should explain why
- `mixed` ownership is only acceptable when the split is named explicitly

Important rule:

- do not generate this from guesses
- derive it from a real integration audit
- then validate it in tests

Recommended workflow:

1. inventory real seams
2. declare surfaces in code
3. call `manifest.validate()` in tests or CI
4. optionally publish `manifest.to_markdown()` into docs

This is not a runtime feature. It is an integration review and documentation hygiene feature.

Primary reference:

- [Compatibility Manifest Guide](https://vcoderun.github.io/acpkit/compatibility-matrix-template/)

## Skill-Local Routing Aids

These files exist to route you quickly into the right part of the codebase or docs set when the
task is narrow:

- [resources/intro.md](resources/intro.md)
- [references/package-surface.md](references/package-surface.md)
- [references/runtime-capabilities.md](references/runtime-capabilities.md)
- [references/docs-examples-map.md](references/docs-examples-map.md)

## Utility Scripts

Use the bundled scripts instead of guessing:

- `python3.11 .agents/skills/acpkit-sdk/scripts/list_public_exports.py`
- `python3.11 .agents/skills/acpkit-sdk/scripts/list_examples.py`

## Working Rules

- Prefer current code over stale memory.
- If docs and code disagree, trust code first and update docs.
- Do not invent ACP surface the runtime cannot actually honor.
- Keep examples runnable, explicit, and strongly typed.
- Treat adapter-owned state and host-owned state as different design choices.
- Prefer the narrowest seam that matches the user’s need.
- Use the compatibility manifest when an integration needs one typed, reviewable ACP surface declaration instead of a loose prose matrix.
- `FileSessionStore` uses `root=Path(...)`.
- `FileSessionStore` is the hardened local durable store, not a distributed session backend.
- Mode slash commands are dynamic, and mode ids must not collide with reserved names such as `model`, `thinking`, `tools`, `hooks`, or `mcp-servers`.
- `run_event_stream` hooks must return async iterables, not coroutines.
