# ACP Kit SDK Intro

ACP Kit is a Python SDK and CLI for turning an existing agent surface into a truthful ACP server boundary.

Today that mostly means exposing `pydantic_ai.Agent` through `pydantic-acp`, while keeping models, modes, plans, approvals, MCP metadata, host tools, and session state aligned with what the underlying runtime can actually support.

This intro is intentionally short. The canonical deep references should come from the docs set in `docs/`, not from a second parallel skill-specific spec.

## Core Positioning

ACP Kit is not a new agent framework.

It sits between:

- an existing agent runtime
- ACP clients such as editors and host applications

The central contract is:

> expose ACP state only when the underlying runtime can actually honor it.

That rule drives model selection, mode switching, slash commands, native plan state, approval flow, MCP metadata, and host-backed tooling.

## Start With The Real Docs

Published docs base URL:

- `https://vcoderun.github.io/acpkit/`

Use these docs pages as the primary references:

| Need | Local source | Published docs |
| --- | --- | --- |
| Product overview and package map | `docs/index.md` | `https://vcoderun.github.io/acpkit/` |
| Construction seams and adapter overview | `docs/pydantic-acp.md` | `https://vcoderun.github.io/acpkit/pydantic-acp/` |
| Runtime config and session ownership | `docs/pydantic-acp/adapter-config.md` | `https://vcoderun.github.io/acpkit/pydantic-acp/adapter-config/` |
| Models, modes, slash commands, thinking | `docs/pydantic-acp/runtime-controls.md` | `https://vcoderun.github.io/acpkit/pydantic-acp/runtime-controls/` |
| Plans, approvals, and cancellation | `docs/pydantic-acp/plans-thinking-approvals.md` | `https://vcoderun.github.io/acpkit/pydantic-acp/plans-thinking-approvals/` |
| Host-owned state patterns | `docs/providers.md` | `https://vcoderun.github.io/acpkit/providers/` |
| ACP-visible extension seams | `docs/bridges.md` | `https://vcoderun.github.io/acpkit/bridges/` |
| Host-backed tools and projections | `docs/host-backends.md` | `https://vcoderun.github.io/acpkit/host-backends/` |
| Maintained example ladder | `docs/examples/index.md` | `https://vcoderun.github.io/acpkit/examples/` |
| Production showcase | `docs/examples/workspace-agent.md` | `https://vcoderun.github.io/acpkit/examples/workspace-agent/` |
| API surface | `docs/api/pydantic_acp.md` | `https://vcoderun.github.io/acpkit/api/pydantic_acp/` |

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
- slash mode commands are dynamic; `ask`, `plan`, and `agent` are examples, not built-in global names
- mode ids must not collide with reserved slash command names like `model`, `thinking`, `tools`, `hooks`, or `mcp-servers`
- only one `PrepareToolsMode(..., plan_mode=True)` is allowed
- `plan_tools=True` is how a non-plan execution mode keeps plan progress tools visible
- `/thinking` only exists when `ThinkingBridge()` is configured
- native ACP plan state and `PlanProvider` are separate ownership paths
- `HookBridge(hide_all=True)` suppresses hook listing output, not the underlying hook capability itself

## Reference Files In This Skill

These skill-local references are only routing aids back into the docs:

- `references/package-surface.md`
- `references/runtime-capabilities.md`
- `references/docs-examples-map.md`

Use them to find the right docs page quickly, not as independent source-of-truth specs.
