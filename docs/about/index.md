# About ACP Kit

ACP Kit exists to turn agent framework APIs into ACP servers without pretending the adapter knows more than the source runtime actually exposes.

ACP Kit is the adapter toolkit and monorepo.

Today its stable production focus is `pydantic-acp`.

Additional adapters such as `langchain-acp` and `dspy-acp` are planned after `pydantic-acp`
reaches 1.0 stability.

## Design Goals

- keep ACP exposure truthful
- preserve native framework semantics when the framework already has them
- keep session state explicit and reviewable
- prefer providers and bridges over hard-coded product assumptions
- make adapter behavior observable in ACP clients

## Current Workspace

The repository currently contains:

- `acpkit`
  root CLI and target resolver
- `pydantic-acp`
  production-grade ACP adapter for `pydantic_ai.Agent`
- `codex-auth-helper`
  Codex-backed model helper for Pydantic AI Responses workflows

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
