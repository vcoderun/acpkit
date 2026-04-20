# About ACP Kit

ACP Kit exists to turn agent framework APIs into ACP servers without pretending the adapter knows more than the source runtime actually exposes.

ACP Kit is the adapter toolkit and monorepo.

Today it ships:

- `pydantic-acp`
- `langchain-acp`
- `acpkit`
- `codex-auth-helper`
- `acpremote`

## Design Goals

- keep ACP exposure truthful
- preserve native framework semantics when the framework already has them
- keep session state explicit and reviewable
- prefer providers and bridges over hard-coded product assumptions
- make adapter behavior observable in ACP clients

## Current Workspace

The repository currently contains two adapter packages, a root CLI package, and two helper packages:

- `pydantic-acp`
  production-grade ACP adapter for `pydantic_ai.Agent`
- `langchain-acp`
  production-grade ACP adapter for LangChain, LangGraph, and DeepAgents graphs
- `acpkit`
  root CLI, target resolver, and launch helpers
- `codex-auth-helper`
  Codex-backed model helper for Pydantic AI Responses workflows
- `acpremote`
  generic ACP transport helper for WebSocket exposure and stdio command mirroring

The helper packages are intentionally adjacent to the adapters rather than
inside them:

- `codex-auth-helper` exists for Codex-backed Pydantic AI usage
- `acpremote` exists for ACP transport and remote mirror workflows

## Intended Audience

ACP Kit is for teams that already have an agent runtime and want:

- a truthful ACP boundary
- editor or client integrations
- host-owned session state where needed
- durable, typed Python seams instead of one-off glue code

## Project Status

The current implementation is production-oriented but still moving quickly. The adapter surface is intentionally explicit so it can evolve without relying on hidden behavior.

## License

ACP Kit is distributed under the Apache 2.0 License.
