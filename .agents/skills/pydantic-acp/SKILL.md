---
name: "pydantic-acp"
description: "Use for `pydantic-acp` tasks: exposing `pydantic_ai.Agent` through ACP, adapter config/runtime ownership, approvals, plans, hooks, projections, host-backed tools, and Pydantic-specific examples."
---

# pydantic-acp Skill

Use this skill when the task is primarily about the `pydantic-acp` adapter package.

This is the richest ACP adapter in the repo and the clearest expression of the project rule:

> expose ACP state only when the underlying runtime can actually honor it.

In this package that rule affects:

- model selection
- mode switching
- config options
- ACP-native plans
- approval flows
- host-backed files and terminal access
- tool projection
- hook visibility
- session replay

## Start Here

If you only need the shortest high-signal path:

1. read `Quick Routing`
2. open `config.py` and `__init__.py` for public-surface questions
3. open `runtime/adapter.py` for lifecycle and dispatch questions
4. then branch into approvals, projections, host, or plans

## Quick Routing

| If the task is about... | Use this skill? | Open first |
| --- | --- | --- |
| `run_acp(agent=...)` or `create_acp_agent(...)` | Yes | `__init__.py`, `config.py`, `runtime/adapter.py` |
| approvals or remembered policy | Yes | `approvals.py`, `runtime/_prompt_execution.py` |
| plans or plan generation | Yes | `bridges/prepare_tools.py`, `runtime/_native_plan_runtime.py`, `models.py` |
| filesystem / terminal ownership | Yes | `host/context.py`, `host/filesystem.py`, `host/terminal.py`, `host/policy.py` |
| hook visibility or hook projection | Yes | `bridges/hooks.py`, `runtime/hook_introspection.py`, `hook_projection.py` |
| slash commands / model / mode surface | Yes | `runtime/slash_commands.py`, `providers.py`, `models.py` |
| Codex auth refresh or `auth.json` | No, pair with `codex-auth-helper` | `packages/helpers/codex-auth-helper/...` |
| remote hosting or WebSocket transport | No, pair with `acpremote` | `packages/transports/acpremote/...` |

## Package Boundary

`pydantic-acp` is the adapter layer for `pydantic_ai.Agent`.

It owns:

- truthful ACP capability advertisement for a Pydantic runtime
- session-scoped model, mode, and config state
- ACP-native plan state and plan updates
- approval lifecycle and remembered approval policies
- hook introspection and hook projection
- host-backed filesystem and terminal ownership
- tool projection maps
- session store semantics and transcript replay

It does not own:

- Codex auth file parsing or token refresh
- WebSocket transport
- root CLI target loading

## Do Not Confuse With

- `pydantic-acp` vs `codex-auth-helper`
  the helper builds a Codex-backed model; this package exposes the Pydantic runtime through ACP
- `pydantic-acp` vs `acpremote`
  this package adapts a Pydantic runtime; `acpremote` only transports ACP
- `pydantic-acp` vs `acpkit-sdk`
  this package owns adapter semantics; `acpkit` owns CLI target loading and dispatch

## Primary References

Package references:

- Raw skill:
  `https://raw.githubusercontent.com/vcoderun/acpkit/main/.agents/skills/pydantic-acp/SKILL.md`
- Raw overview docs:
  `https://raw.githubusercontent.com/vcoderun/acpkit/main/docs/pydantic-acp.md`
- Raw host backends docs:
  `https://raw.githubusercontent.com/vcoderun/acpkit/main/docs/host-backends.md`
- Raw projection cookbook:
  `https://raw.githubusercontent.com/vcoderun/acpkit/main/docs/projection-cookbook.md`
- Raw prompt/resources docs:
  `https://raw.githubusercontent.com/vcoderun/acpkit/main/docs/pydantic-acp/prompt-resources.md`
- Rendered overview:
  `https://vcoderun.github.io/acpkit/pydantic-acp/`
- Source tree:
  `https://github.com/vcoderun/acpkit/tree/main/packages/adapters/pydantic-acp`

Cross-skill references:

- Root package skill:
  `https://raw.githubusercontent.com/vcoderun/acpkit/main/.agents/skills/acpkit-sdk/SKILL.md`
- Codex helper skill:
  `https://raw.githubusercontent.com/vcoderun/acpkit/main/.agents/skills/codex-auth-helper/SKILL.md`
- Remote transport skill:
  `https://raw.githubusercontent.com/vcoderun/acpkit/main/.agents/skills/acpremote/SKILL.md`

## Public Surface

High-value public seams:

- `run_acp(agent=...)`
- `create_acp_agent(...)`
- `AdapterConfig(...)`
- `MemorySessionStore`
- `FileSessionStore`
- `NativeApprovalBridge`
- `ClientHostContext`
- `CompatibilityManifest`
- `BlackBoxHarness`

Package entrypoint:

- `https://github.com/vcoderun/acpkit/blob/main/packages/adapters/pydantic-acp/src/pydantic_acp/__init__.py`

## Module Guide

| Subsystem | Key files | Use them for |
| --- | --- | --- |
| public surface and construction | `__init__.py`, `config.py`, `agent_source.py`, `agent_types.py`, `models.py`, `providers.py` | public API shape, construction seams, provider contracts |
| approvals | `approvals.py`, `runtime/_prompt_execution.py`, `runtime/_prompt_runtime.py` | deferred approvals, remembered policy, permission flow |
| bridges | `bridges/base.py`, `bridges/capability_support.py`, `bridges/history_processor.py`, `bridges/hooks.py`, `bridges/mcp.py`, `bridges/prepare_tools.py`, `bridges/thinking.py` | optional capability wiring and extension seams |
| projection | `projection.py`, `projection_helpers.py`, `_projection_text.py`, `_projection_risk.py`, `hook_projection.py` | ACP-visible transcript cards and rendering |
| host ownership | `host/context.py`, `host/filesystem.py`, `host/terminal.py`, `host/policy.py`, `host/_policy_paths.py`, `host/_policy_commands.py` | path safety, command safety, client-backed host behavior |
| runtime core | `runtime/adapter.py`, `runtime/server.py`, `runtime/bridge_manager.py`, `runtime/hook_introspection.py`, `runtime/session_surface.py`, `runtime/slash_commands.py` | adapter lifecycle, runtime update emission, slash command behavior |
| runtime helpers | `_adapter_mixins.py`, `_adapter_prompt.py`, `_agent_state.py`, `_native_plan_runtime.py`, `_prompt_model_runtime.py`, `_session_lifecycle.py`, `_session_model_runtime.py`, `_session_runtime.py`, `_session_surface_runtime.py` | narrower runtime bugs that need subsystem-level edits |
| session storage | `session/state.py`, `session/store.py` | persisted sessions, replay, load/fork/resume/close/list |
| ACP testing helpers | `testing/fakes.py`, `testing/harness.py` | testing the ACP boundary itself |

## Construction Seams

### `run_acp(agent=...)`

Use this when the caller already has one agent instance and wants the narrowest path to a running
ACP server.

### `create_acp_agent(...)`

Use this when the ACP-compatible agent object is needed before it is run.

Common reasons:

- combine with `acpremote`
- embed into another runner
- test the ACP object directly

### `agent_factory=`

Use this when ACP session state should influence agent construction but full custom source control
is unnecessary.

Typical cases:

- model-aware variants
- workspace-root binding
- mode-aware tools or instructions

### `agent_source=`

Use this when the caller needs total control over agent materialization.

Typical cases:

- precise host-context injection
- complex dependency wiring
- custom source behavior that outgrows a factory callback

## Plans, Modes, Models, and Slash Commands

`pydantic-acp` is the repo's most complete ACP-native session surface.

It supports:

- model selection
- mode switching
- config options
- ACP-native plans
- tool-based or structured plan generation
- session replay and fork/resume/load/close/list lifecycle
- slash command discovery and rendering

This package should be the reference answer whenever the question is:

- "can ACP expose model switching truthfully?"
- "where do slash commands come from?"
- "how does plan state survive reload?"

## Bridges and Projections

High-value bridges include:

- `ThreadExecutorBridge`
- `SetToolMetadataBridge`
- `IncludeToolReturnSchemasBridge`
- `WebSearchBridge`
- `WebFetchBridge`
- `ImageGenerationBridge`
- `McpCapabilityBridge`
- `ToolsetBridge`
- `PrefixToolsBridge`
- `OpenAICompactionBridge`
- `AnthropicCompactionBridge`

High-value projection families include:

- `FileSystemProjectionMap`
- `WebToolProjectionMap`
- `BuiltinToolProjectionMap`
- `HookProjectionMap`
- `CompositeProjectionMap`

Important rule:

- bridges affect runtime behavior and metadata
- projection maps affect ACP-visible transcript rendering

Split those concerns before editing.

## Host Ownership

This package has the repo's strongest host-side ownership model.

Relevant public ideas:

- `ClientHostContext`
- filesystem backend
- terminal backend
- `HostAccessPolicy`

Use this skill when the task is about:

- safe writes
- command warnings
- diff projection before approval
- path normalization
- client-owned host resources

## Prompt and Model Ownership

This package also owns the more subtle Pydantic-specific surfaces:

- prompt-to-input conversion
- prompt-model override providers
- media-aware model routing
- transcript-to-history rebuilding
- model restore paths during replay/load

When the question involves image/audio/resources plus model selection, this package is usually the
correct home.

## Common Workflows

### Minimal Pydantic ACP server

```python
from pydantic_ai import Agent
from pydantic_acp import run_acp

agent = Agent('openai:gpt-5')
run_acp(agent=agent)
```

### ACP object first, run later

Use `create_acp_agent(...)` when another runner or transport layer should own startup.

### Session-aware construction

Use `agent_factory=` when session state should change the built agent.

### Codex-backed Pydantic model plus ACP

Use `codex-auth-helper` to construct the model, then expose through `pydantic-acp`.

### Remote-hosted Pydantic ACP

Adapt with `pydantic-acp`, then expose with `acpremote`.

## Public Examples

Maintained public examples:

- `https://raw.githubusercontent.com/vcoderun/acpkit/main/examples/pydantic/README.md`
- `https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/finance_agent.py`
- `https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/travel_agent.py`

Use `finance_agent.py` for:

- ACP-native plans
- approvals
- projected file diffs
- workspace/file ownership

Use `travel_agent.py` for:

- hook projection
- prompt-model overrides
- media prompt behavior

Skill-local example index:

- `https://github.com/vcoderun/acpkit/blob/main/.agents/skills/pydantic-acp/examples/README.md`

Cross-package Codex-backed example:

- `https://github.com/vcoderun/acpkit/blob/main/.agents/skills/codex-auth-helper/examples/codex_responses_agent.py`

## Handoff Rules

Pair or switch to:

- `codex-auth-helper`
  when a local Codex login is being turned into a Pydantic AI model
- `acpkit-sdk`
  when this adapter is being reached through `acpkit run ...` or `acpkit serve ...`
- `acpremote`
  when the adapted agent is then exposed remotely over WebSocket

Stay in this skill when the main issue is:

- ACP runtime truthfulness
- provider state
- plan/approval behavior
- host policy
- projection
- session lifecycle

## Guardrails

- Do not describe `pydantic-acp` as transport.
- Do not promise ACP state the active `pydantic_ai.Agent` cannot honor.
- Do not route LangChain or DeepAgents questions through this skill.
- Do not answer Codex auth refresh questions from here unless the adapter integration itself is
  the point.
- If the task is really about remote transport, move to `acpremote`.
