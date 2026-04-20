---
name: "langchain-acp"
description: "Use for `langchain-acp` tasks: exposing LangChain, LangGraph, and DeepAgents graphs through ACP, graph/session construction, plans, bridges, projections, and LangChain-specific examples."
---

# langchain-acp Skill

Use this skill when the task is centered on the `langchain-acp` adapter package.

This package is the LangChain-side ACP adapter boundary in the repo. Treat it as a first-class
adapter, not as a secondary package behind Pydantic.

It owns:

- LangGraph/LangChain graph adaptation
- session-aware graph rebuilding
- provider-backed model/mode/config state
- tool and event projection for stable LangChain tool families
- DeepAgents compatibility
- ACP-native plan extraction from graph state and tool activity

## Start Here

If you only need the shortest high-signal path:

1. read `Quick Routing`
2. open the [adapter config module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/config.py) and the [package entrypoint](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/__init__.py) for public-surface questions
3. open the [runtime adapter](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/runtime/adapter.py) for lifecycle and dispatch questions
4. then branch into graph build, projections, or plan runtime

## Quick Routing

| If the task is about... | Use this skill? | Open first |
| --- | --- | --- |
| `run_acp(graph=...)` or `create_acp_agent(...)` | Yes | [package entrypoint](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/__init__.py), [adapter config module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/config.py), [runtime adapter](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/runtime/adapter.py) |
| session-aware graph rebuilding | Yes | [graph source module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/graph_source.py), [graph builder](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/builders/graph.py), [providers module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/providers.py) |
| DeepAgents compatibility | Yes | [built-in bridge module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/bridges/builtin.py), [projection module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/projection.py), public examples |
| search/browser/http/file/finance projection presets | Yes | [projection module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/projection.py), [event projection module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/event_projection.py) |
| plan extraction or plan persistence | Yes | [plan module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/plan.py), [native plan runtime](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/runtime/_native_plan_runtime.py), [session store](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/session/store.py) |
| root CLI import/dispatch behavior | No, pair with `acpkit-sdk` | [root runtime package](https://github.com/vcoderun/acpkit/tree/main/src/acpkit) |
| WebSocket transport or remote mirroring | No, pair with `acpremote` | [remote transport package](https://github.com/vcoderun/acpkit/tree/main/packages/transports/acpremote) |

## Package Boundary

`langchain-acp` adapts LangChain-family graph runtimes into ACP.

It owns:

- how ACP session state becomes graph build input
- provider-backed model/mode/config state
- how graph outputs and tool activity become ACP updates
- how stable tool families get first-class projections
- how DeepAgents surfaces are normalized
- how native plan state is extracted and persisted

It does not own:

- root CLI target resolution
- Codex auth handling
- WebSocket transport

## Do Not Confuse With

- `langchain-acp` vs `pydantic-acp`
  this package adapts graph runtimes, not `pydantic_ai.Agent`
- `langchain-acp` vs `acpremote`
  this package adapts LangChain-family graphs; `acpremote` only transports ACP
- `langchain-acp` vs `acpkit-sdk`
  this package owns adapter semantics; `acpkit` owns CLI target loading and dispatch

## Primary References

Package references:

- [Raw skill](https://raw.githubusercontent.com/vcoderun/acpkit/main/.agents/skills/langchain-acp/SKILL.md)
- [Raw overview docs](https://raw.githubusercontent.com/vcoderun/acpkit/main/docs/langchain-acp.md)
- [Raw projections docs](https://raw.githubusercontent.com/vcoderun/acpkit/main/docs/langchain-acp/projections.md)
- [Raw providers docs](https://raw.githubusercontent.com/vcoderun/acpkit/main/docs/langchain-acp/providers.md)
- [Rendered overview](https://vcoderun.github.io/acpkit/langchain-acp/)
- [Source tree](https://github.com/vcoderun/acpkit/tree/main/packages/adapters/langchain-acp)

Cross-skill references:

- [Root package skill](https://raw.githubusercontent.com/vcoderun/acpkit/main/.agents/skills/acpkit-sdk/SKILL.md)
- [Remote transport skill](https://raw.githubusercontent.com/vcoderun/acpkit/main/.agents/skills/acpremote/SKILL.md)

## Public Surface

High-value public seams:

- `run_acp(graph=...)`
- `create_acp_agent(...)`
- `AdapterConfig(...)`
- `GraphSource`
- `StaticGraphSource`
- `FactoryGraphSource`
- session stores
- projection maps
- event projection maps
- bridge manager and built-in bridges

Package entrypoint:

- [Package entrypoint](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/__init__.py)

## Module Guide

| Subsystem | Key files | Use them for |
| --- | --- | --- |
| public config and graph source | [package entrypoint](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/__init__.py), [adapter config module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/config.py), [graph source module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/graph_source.py), [providers module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/providers.py), [shared types module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/types.py) | public API shape, graph source selection, provider contracts |
| graph building and bridge management | [graph builder](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/builders/graph.py), [bridge manager](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/bridge_manager.py), [base bridge module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/bridges/base.py), [built-in bridge module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/bridges/builtin.py) | graph augmentation, built-in compatibility contributions, bridge wiring |
| projection | [projection module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/projection.py), [event projection module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/event_projection.py), [serialization module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/serialization.py) | search/http/browser/command/file/finance rendering and event rendering |
| plans and session state | [plan module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/plan.py), [native plan runtime](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/runtime/_native_plan_runtime.py), [session-state module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/session/state.py), [session-store module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/session/store.py) | plan extraction, persistence, replay, stored updates |
| runtime core | [runtime adapter](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/runtime/adapter.py), [runtime server](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/runtime/server.py), [prompt-conversion runtime](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/runtime/_prompt_conversion.py), [approvals module](https://github.com/vcoderun/acpkit/blob/main/packages/adapters/langchain-acp/src/langchain_acp/approvals.py) | prompt conversion, approval routing, ACP session operations, runtime updates |

## Construction Seams

### `run_acp(graph=...)`

Use this when one graph instance is already enough and the narrowest path to a running ACP server
is desired.

### `create_acp_agent(...)`

Use this when the ACP-compatible agent object is needed before it is run.

Typical reasons:

- combine with `acpremote`
- embed into another runner
- test the ACP boundary directly

### `graph_factory=`

Use this when ACP session state should influence graph construction.

Typical reasons:

- session-root workspace binding
- provider-selected model changes
- mode-specific graph structure

### `graph_source=`

Use this when a fully explicit graph source abstraction is more appropriate than a factory callback.

## Projection Strategy

This package has its own projection story. Do not explain it with Pydantic-only terms.

High-value projection families include:

- `DeepAgentsProjectionMap`
- `WebSearchProjectionMap`
- `HttpRequestProjectionMap`
- `WebFetchProjectionMap`
- `BrowserProjectionMap`
- `CommandProjectionMap`
- `CommunityFileManagementProjectionMap`
- `FinanceProjectionMap`
- `StructuredEventProjectionMap`

Important rule:

- stable `langchain-community` tool families are good preset candidates
- provider built-in tools are often more heterogeneous and should be handled more conservatively

## Bridges and Graph Contributions

Important extension seams:

- `CapabilityBridge`
- built-in bridge manager contributions
- `DeepAgentsCompatibilityBridge`
- provider-backed model/mode/config contributions

Use this package when the question is:

- how ACP-visible capabilities get attached to a graph build
- how compatibility layers affect the runtime
- how session state influences graph construction

## Plans and Session Lifecycle

This package supports:

- session-aware graph rebuilding
- session stores and replay
- provider-backed models/modes/configs
- native ACP plan runtime
- tool-based or structured plan extraction

The governing rule remains:

- only expose ACP state the graph/runtime can actually honor

## Common Workflows

### Minimal LangChain ACP server

```python
from langchain.agents import create_agent
from langchain_acp import run_acp

graph = create_agent(model='openai:gpt-5', tools=[])
run_acp(graph=graph)
```

### ACP object first, run later

Use `create_acp_agent(...)` when another runner or transport layer should own startup.

### Session-aware graph factory

Use `graph_factory=` when session state should change the graph build.

### Remote-hosted LangChain ACP

Adapt with `langchain-acp`, then expose with `acpremote`.

## Public Examples

Maintained public examples:

- [LangChain public examples](https://raw.githubusercontent.com/vcoderun/acpkit/main/examples/langchain/README.md)
- [Workspace graph example](https://github.com/vcoderun/acpkit/blob/main/examples/langchain/workspace_graph.py)
- [DeepAgents graph example](https://github.com/vcoderun/acpkit/blob/main/examples/langchain/deepagents_graph.py)

Use the [workspace graph example](https://github.com/vcoderun/acpkit/blob/main/examples/langchain/workspace_graph.py) for:

- module-level `graph`
- session-aware `graph_from_session(...)`
- filesystem projection
- `acpkit run ...` and `acpkit serve ...` integration

Use the [DeepAgents graph example](https://github.com/vcoderun/acpkit/blob/main/examples/langchain/deepagents_graph.py) for:

- DeepAgents compatibility
- `DeepAgentsCompatibilityBridge`
- `DeepAgentsProjectionMap`

Skill-local example index:

- [Skill-local example index](https://github.com/vcoderun/acpkit/blob/main/.agents/skills/langchain-acp/examples/README.md)

Remote-host recipe references:

- [Remote command exposure recipe](https://github.com/vcoderun/acpkit/blob/main/.agents/skills/acpremote/examples/serve_command.py)
- [Remote mirror recipe](https://github.com/vcoderun/acpkit/blob/main/.agents/skills/acpremote/examples/mirror_remote.py)

## Handoff Rules

Pair or switch to:

- `acpkit-sdk`
  when the graph is reached through `acpkit run ...` or `acpkit serve ...`
- `acpremote`
  when the adapted graph is then exposed remotely over WebSocket transport

Stay in this skill when the main issue is:

- graph construction
- provider-backed runtime state
- LangChain-side tool or event projection
- DeepAgents compatibility
- plan extraction or session replay

## Guardrails

- Do not describe LangChain support as secondary to Pydantic.
- Do not reuse Pydantic-only host-policy language when the bug is really about graph or tool seams.
- Do not claim a projection preset exists for an unstable tool family unless it is implemented.
- If the task is really transport-only, move to `acpremote`.
