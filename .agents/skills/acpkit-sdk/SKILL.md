---
name: "acpkit-sdk"
description: "Use for ACP Kit root-package work: CLI target resolution, `acpkit run`, `acpkit serve`, `acpkit launch`, cross-package routing, and end-to-end flows that span adapters plus transport."
---

# acpkit Root Skill

Use this skill when the task is primarily about the root `acpkit` package.

This skill owns the repo-level orchestration layer:

- CLI entrypoints
- Python target loading
- adapter auto-dispatch
- native ACP passthrough
- `--addr` remote mirror entrypoints
- Toad launch integration
- end-to-end flows that combine the root package with adapters and transport

It does not own the detailed runtime semantics of the adapter packages.

## Start Here

If you only need the shortest high-signal path:

1. read `Quick Routing`
2. open the [CLI module](https://github.com/vcoderun/acpkit/blob/main/src/acpkit/cli.py) for command-shape questions
3. open the [runtime module](https://github.com/vcoderun/acpkit/blob/main/src/acpkit/runtime.py) for execution-path questions
4. open the [adapter-dispatch module](https://github.com/vcoderun/acpkit/blob/main/src/acpkit/adapters.py) for adapter-selection questions

## Quick Routing

| If the task is about... | Use this skill? | Open first |
| --- | --- | --- |
| `acpkit run ...` or `acpkit serve ...` | Yes | [CLI module](https://github.com/vcoderun/acpkit/blob/main/src/acpkit/cli.py), [runtime module](https://github.com/vcoderun/acpkit/blob/main/src/acpkit/runtime.py) |
| `module` vs `module:attribute` target resolution | Yes | [CLI module](https://github.com/vcoderun/acpkit/blob/main/src/acpkit/cli.py), [runtime module](https://github.com/vcoderun/acpkit/blob/main/src/acpkit/runtime.py) |
| missing adapter install hints | Yes | [adapter-dispatch module](https://github.com/vcoderun/acpkit/blob/main/src/acpkit/adapters.py) |
| native ACP passthrough | Yes | [adapter-dispatch module](https://github.com/vcoderun/acpkit/blob/main/src/acpkit/adapters.py), [runtime module](https://github.com/vcoderun/acpkit/blob/main/src/acpkit/runtime.py) |
| `acpkit launch ...` or `launch --command ...` | Yes | [runtime module](https://github.com/vcoderun/acpkit/blob/main/src/acpkit/runtime.py), [CLI module](https://github.com/vcoderun/acpkit/blob/main/src/acpkit/cli.py) |
| `pydantic_ai.Agent` runtime behavior | No, pair with `pydantic-acp` | [Pydantic adapter package](https://github.com/vcoderun/acpkit/tree/main/packages/adapters/pydantic-acp) |
| LangGraph / DeepAgents runtime behavior | No, pair with `langchain-acp` | [LangChain adapter package](https://github.com/vcoderun/acpkit/tree/main/packages/adapters/langchain-acp) |
| WebSocket transport or remote mirroring | No, pair with `acpremote` | [Remote transport package](https://github.com/vcoderun/acpkit/tree/main/packages/transports/acpremote) |

## Package Boundary

`acpkit` is not an adapter and not a transport package.

It is the root runtime package that:

1. accepts CLI input
2. resolves a target from Python import space or remote address
3. selects the correct runtime lane
4. dispatches to the relevant package

That means it owns control flow, not framework semantics.

What it owns:

- CLI parsing
- target loading
- adapter detection
- dispatch to `pydantic-acp` or `langchain-acp`
- direct passthrough for already-materialized ACP agents
- remote mirror startup through `--addr`
- Toad launch wrapping

What it does not own:

- Pydantic plan/approval/projection details
- LangChain graph/provider/projection details
- WebSocket transport behavior
- Codex auth parsing

## Primary References

Root-package references:

- [Raw skill](https://raw.githubusercontent.com/vcoderun/acpkit/main/.agents/skills/acpkit-sdk/SKILL.md)
- [Raw CLI docs](https://raw.githubusercontent.com/vcoderun/acpkit/main/docs/cli.md)
- [Raw examples index](https://raw.githubusercontent.com/vcoderun/acpkit/main/docs/examples/index.md)
- [Rendered CLI docs](https://vcoderun.github.io/acpkit/cli/)
- [Source tree](https://github.com/vcoderun/acpkit/tree/main/src/acpkit)

Cross-package references often needed from this skill:

- [Pydantic adapter skill](https://raw.githubusercontent.com/vcoderun/acpkit/main/.agents/skills/pydantic-acp/SKILL.md)
- [LangChain adapter skill](https://raw.githubusercontent.com/vcoderun/acpkit/main/.agents/skills/langchain-acp/SKILL.md)
- [Remote transport skill](https://raw.githubusercontent.com/vcoderun/acpkit/main/.agents/skills/acpremote/SKILL.md)
- [Codex helper skill](https://raw.githubusercontent.com/vcoderun/acpkit/main/.agents/skills/codex-auth-helper/SKILL.md)

## Public Entry Points

User-facing commands:

- `acpkit run TARGET`
- `acpkit run --addr ws://...`
- `acpkit serve TARGET`
- `acpkit launch TARGET`
- `acpkit launch --command "..."`

Important runtime helpers:

- `run_target(...)`
- `serve_target(...)`
- `launch_target(...)`
- `run_remote_addr(...)`

Important distinction:

- `run TARGET`
  starts a local stdio ACP server from a Python target
- `serve TARGET`
  materializes a compatible ACP agent and exposes it through `acpremote`
- `run --addr ...`
  skips Python target resolution entirely and mirrors a remote ACP endpoint locally
- `launch`
  wraps the `run` path through Toad or shells out directly for command mode

## Target Resolution

High-level target resolution flow:

1. add the current working directory to `sys.path`
2. add any explicit `-p/--path` roots
3. import the module
4. if an attribute path exists, resolve it
5. if no attribute path exists, select the last defined supported target

Current supported target families:

- `pydantic_ai.Agent`
- LangGraph / LangChain compiled graphs
- native ACP agents

When debugging target resolution:

1. inspect import roots first
2. inspect the resolved object type second
3. inspect adapter installation/import errors third

Do not jump into adapter internals before confirming the root package selected the correct object.

## Adapter Dispatch

The root package decides the runtime lane, then hands off.

Conceptual dispatch:

1. if the target already satisfies the ACP agent boundary, pass it through
2. if the target is a `pydantic_ai.Agent`, materialize through `pydantic-acp`
3. if the target is a LangGraph/LangChain graph, materialize through `langchain-acp`
4. if the user passed `--addr`, bypass target loading and mirror the remote ACP endpoint

`acpkit` should stay explicit about:

- target typing
- adapter availability
- dispatch selection

It should stay intentionally ignorant about:

- Pydantic adapter internals
- LangChain adapter internals
- WebSocket wire semantics

## Do Not Confuse With

- `acpkit-sdk` vs `pydantic-acp`
  `acpkit` decides the lane; `pydantic-acp` owns the Pydantic runtime semantics
- `acpkit-sdk` vs `langchain-acp`
  `acpkit` resolves and dispatches; `langchain-acp` owns graph adaptation and projection
- `acpkit-sdk` vs `acpremote`
  `acpkit` is the root CLI/runtime package; `acpremote` is transport

## Module Guide

| Module | Use it for | Notes |
| --- | --- | --- |
| [CLI module](https://github.com/vcoderun/acpkit/blob/main/src/acpkit/cli.py) | Click command definitions, argument validation, help/flag behavior | First stop for CLI UX bugs |
| [Runtime module](https://github.com/vcoderun/acpkit/blob/main/src/acpkit/runtime.py) | `run`, `serve`, `launch`, remote mirror runtime paths | First stop for dispatch behavior after parsing |
| [Adapter-dispatch module](https://github.com/vcoderun/acpkit/blob/main/src/acpkit/adapters.py) | target typing, adapter import/load, native ACP detection | First stop for wrong lane selection |
| [Compatibility helpers](https://github.com/vcoderun/acpkit/blob/main/src/acpkit/compatibility.py) | compatibility manifest helpers | Not part of normal runtime dispatch |
| [Compatibility schema](https://github.com/vcoderun/acpkit/blob/main/src/acpkit/_compatibility_schema.py) | manifest schema and validation | Use only for compatibility-surface work |
| [Console entrypoint](https://github.com/vcoderun/acpkit/blob/main/src/acpkit/__main__.py) | console entrypoint | Usually not the source of behavior bugs |

## Common Workflows

### Run an adapter-backed local ACP server

Pydantic:

```bash
acpkit run examples.pydantic.finance_agent:agent
```

LangChain:

```bash
acpkit run examples.langchain.workspace_graph:graph
```

### Expose a remote ACP host

```bash
acpkit serve examples.langchain.workspace_graph:graph --host 0.0.0.0 --port 8080
```

### Mirror a remote ACP host back into a local ACP boundary

```bash
acpkit run --addr ws://127.0.0.1:8080/acp/ws
```

### Launch through Toad

```bash
acpkit launch examples.pydantic.finance_agent:agent
acpkit launch examples.langchain.workspace_graph:graph
```

### Launch a script that already starts ACP itself

```bash
acpkit launch --command "python3.11 some_script_that_starts_acp.py"
```

## Cross-Package Workflows

Typical combinations:

- `acpkit` + `pydantic-acp`
  when the root package resolves and runs or serves a `pydantic_ai.Agent`
- `acpkit` + `langchain-acp`
  when the root package resolves and runs or serves a LangChain/LangGraph target
- `acpkit` + `acpremote`
  when the root package is part of a remote-host topology via `serve` or `--addr`
- `pydantic-acp` + `codex-auth-helper`
  when a Codex-backed Pydantic model is exposed through ACP and then possibly launched through `acpkit`

## Skill-Bundled Recipes

Skill-local recipe index:

- [Skill-local recipe index](https://github.com/vcoderun/acpkit/blob/main/.agents/skills/acpkit-sdk/examples/README.md)

Public adapter examples commonly launched through the root package:

- [Pydantic public examples](https://raw.githubusercontent.com/vcoderun/acpkit/main/examples/pydantic/README.md)
- [LangChain public examples](https://raw.githubusercontent.com/vcoderun/acpkit/main/examples/langchain/README.md)

Remote pairing examples:

- [Remote command exposure recipe](https://github.com/vcoderun/acpkit/blob/main/.agents/skills/acpremote/examples/serve_command.py)
- [Remote mirror recipe](https://github.com/vcoderun/acpkit/blob/main/.agents/skills/acpremote/examples/mirror_remote.py)

## Handoff Rules

Switch to a narrower skill when:

- the bug is clearly inside adapter runtime behavior
- the task is about approvals, plans, projections, or host policy
- the task is about transport or remote ownership rather than CLI or dispatch
- the task is about Codex auth refresh or `auth.json`

Stay in this skill when:

- the main question is import resolution
- the main question is runtime lane selection
- the main question is CLI behavior
- the main question is how multiple packages fit together end-to-end

## Guardrails

- Do not claim the root package itself adapts framework runtimes. It routes them.
- Do not describe `acpremote` as an adapter.
- Do not describe `codex-auth-helper` as part of target resolution.
- Do not document a root CLI feature that is not present in the [CLI module](https://github.com/vcoderun/acpkit/blob/main/src/acpkit/cli.py).
- When the question is about adapter truthfulness, plans, approvals, projections, host ownership,
  or provider behavior, move to the narrower package skill.
