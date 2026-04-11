# ACP Kit SDK Intro

ACP Kit is the adapter toolkit and monorepo for turning an existing agent surface into a truthful ACP server boundary.

Today the stable production focus is `pydantic-acp`: exposing `pydantic_ai.Agent` through ACP while keeping models, modes, plans, approvals, MCP metadata, host tools, and session state aligned with what the underlying runtime can actually support.

Additional adapters such as `langchain-acp` and `dspy-acp` are planned after `pydantic-acp` reaches 1.0 stability.

This intro is intentionally short. The canonical deep references should come from the published docs set, not from a second parallel skill-specific spec.

## Core Positioning

ACP Kit is not a new agent framework.

It sits between:

- an existing agent runtime
- ACP clients such as editors and host applications

The central contract is:

> expose ACP state only when the underlying runtime can actually honor it.

That rule drives model selection, mode switching, slash commands, native plan state, approval flow, MCP metadata, and host-backed tooling.

## Start With The Real Docs

Use these published docs pages as the primary references:

| Need | Published docs |
| --- | --- |
| Product overview and package map | [ACP Kit Overview](https://vcoderun.github.io/acpkit/) |
| Construction seams and adapter overview | [Pydantic ACP Overview](https://vcoderun.github.io/acpkit/pydantic-acp/) |
| Runtime config and session ownership | [AdapterConfig](https://vcoderun.github.io/acpkit/pydantic-acp/adapter-config/) |
| Models, modes, slash commands, thinking | [Models, Modes, and Slash Commands](https://vcoderun.github.io/acpkit/pydantic-acp/runtime-controls/) |
| Plans, approvals, and cancellation | [Plans, Thinking, and Approvals](https://vcoderun.github.io/acpkit/pydantic-acp/plans-thinking-approvals/) |
| Host-owned state patterns | [Providers](https://vcoderun.github.io/acpkit/providers/) |
| ACP-visible extension seams | [Bridges](https://vcoderun.github.io/acpkit/bridges/) |
| Host-backed tools and projections | [Host Backends and Projections](https://vcoderun.github.io/acpkit/host-backends/) |
| Maintained example ladder | [Examples Overview](https://vcoderun.github.io/acpkit/examples/) |
| Production showcase | [Workspace Agent](https://vcoderun.github.io/acpkit/examples/workspace-agent/) |
| API surface | [pydantic_acp API](https://vcoderun.github.io/acpkit/api/pydantic_acp/) |

## Construction Seams To Reach For

Use these seams intentionally:

| Seam | Use it when |
| --- | --- |
| `run_acp(agent=...)` | you want the smallest direct path from `pydantic_ai.Agent` to a running ACP server |
| `create_acp_agent(...)` | you need the ACP-compatible agent object before running it |
| `agent_factory=` | session context should influence agent construction, but a full custom source is unnecessary |
| `agent_source=` | you need full control over agent build path, host binding, and session-specific dependencies |
| built-in `AdapterConfig` fields | the adapter can own the relevant session state cleanly |
| providers | the host or product layer should remain the source of truth |
| bridges | the runtime needs ACP-visible capabilities without hard-coding them into the adapter core |

## High-Value Guardrails

- `FileSessionStore` takes `root=Path(...)`, not `base_dir=...`
- `FileSessionStore` is the hardened local durable store: atomic replace writes, local locking, malformed-session tolerance, and stale temp cleanup; it is not a distributed multi-writer backend
- slash mode commands are dynamic; `ask`, `plan`, and `agent` are examples, not built-in global names
- mode ids must not collide with reserved slash command names like `model`, `thinking`, `tools`, `hooks`, or `mcp-servers`
- only one `PrepareToolsMode(..., plan_mode=True)` is allowed
- `plan_tools=True` is how a non-plan execution mode keeps plan progress tools visible
- `/thinking` only exists when `ThinkingBridge()` is configured
- native ACP plan state and `PlanProvider` are separate ownership paths
- `HookBridge(hide_all=True)` suppresses hook listing output, not the underlying hook capability itself
- custom `run_event_stream` hooks and wrappers must return an async iterable, not a coroutine

## Reference Files In This Skill

These skill-local references are only routing aids back into the docs:

- `references/package-surface.md`
- `references/runtime-capabilities.md`
- `references/docs-examples-map.md`

Use them to find the right docs page quickly, not as independent source-of-truth specs.
