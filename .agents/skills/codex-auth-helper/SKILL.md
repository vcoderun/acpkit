---
name: "codex-auth-helper"
description: "Use for `codex-auth-helper` tasks: reading local Codex auth state, refreshing tokens, building Codex-backed Responses clients/models, and wiring them into `pydantic-ai`, `pydantic-acp`, or LangChain."
---

# codex-auth-helper Skill

Use this skill when the task is centered on the `codex-auth-helper` package.

This package exists so the repo does not re-implement Codex auth and Responses wiring ad hoc in
examples, adapters, or one-off scripts.

It is intentionally narrow:

- read existing Codex auth state
- refresh tokens when needed
- derive account-scoped request headers
- build a Codex-specific async OpenAI client
- build a `pydantic-ai` Responses model on top of that client
- build a LangChain `ChatOpenAI` model on top of the same auth/session flow

## Start Here

If you only need the shortest high-signal path:

1. read `Quick Routing`
2. open the [factory module](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/factory.py) for public constructor behavior
3. open the [token manager](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/auth/manager.py) for refresh logic
4. open the [auth-state module](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/auth/state.py) and the [auth-store module](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/auth/store.py) for auth payload shape and persistence

## Quick Routing

| If the task is about... | Use this skill? | Open first |
| --- | --- | --- |
| `~/.codex/auth.json` parsing | Yes | [auth-state module](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/auth/state.py), [auth-store module](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/auth/store.py) |
| token refresh timing or refresh requests | Yes | [token manager](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/auth/manager.py), [auth-config module](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/auth/config.py) |
| Codex-specific request headers or account id | Yes | [Codex client module](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/client.py), [auth-state module](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/auth/state.py) |
| building a `CodexResponsesModel` | Yes | [factory module](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/factory.py), [model module](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/model.py) |
| building a LangChain `ChatOpenAI` | Yes | [factory module](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/factory.py), [Codex client module](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/client.py) |
| exposing that model through ACP | Pair with `pydantic-acp` | [Pydantic adapter package](https://github.com/vcoderun/acpkit/tree/main/packages/adapters/pydantic-acp) |
| exposing a Codex-backed LangChain graph through ACP | Pair with `langchain-acp` | [LangChain adapter package](https://github.com/vcoderun/acpkit/tree/main/packages/adapters/langchain-acp) |
| WebSocket transport | No, pair with `acpremote` | [remote transport package](https://github.com/vcoderun/acpkit/tree/main/packages/transports/acpremote) |

## Package Boundary

This package is not:

- an ACP adapter
- a CLI package
- a transport package

It is a helper for turning a local Codex login into a reusable `pydantic-ai` Responses model.

That means it owns:

- auth file parsing
- token refresh timing
- account-id extraction
- Codex request header shaping
- Responses-model construction

## Do Not Confuse With

- `codex-auth-helper` vs `pydantic-acp`
  this package builds the model/client; `pydantic-acp` exposes the runtime through ACP
- `codex-auth-helper` vs `acpkit-sdk`
  this package has no CLI target-loading role
- `codex-auth-helper` vs `acpremote`
  this package has no transport role

It does not own:

- ACP session lifecycle
- approvals
- transport
- CLI dispatch

## Primary References

Package references:

- [Raw skill](https://raw.githubusercontent.com/vcoderun/acpkit/main/.agents/skills/codex-auth-helper/SKILL.md)
- [Raw helpers docs](https://raw.githubusercontent.com/vcoderun/acpkit/main/docs/helpers.md)
- [Raw API docs page](https://raw.githubusercontent.com/vcoderun/acpkit/main/docs/api/codex_auth_helper.md)
- [Rendered helper docs](https://vcoderun.github.io/acpkit/helpers/)
- [Source tree](https://github.com/vcoderun/acpkit/tree/main/packages/helpers/codex-auth-helper)

Cross-skill reference:

- [Pydantic adapter skill](https://raw.githubusercontent.com/vcoderun/acpkit/main/.agents/skills/pydantic-acp/SKILL.md)

## Public Surface

High-value public seams:

- `create_codex_responses_model(...)`
- `create_codex_async_openai(...)`
- `create_codex_openai(...)`
- `create_codex_chat_openai(...)`
- `CodexAsyncOpenAI`
- `CodexOpenAI`
- `CodexResponsesModel`
- `CodexAuthConfig`
- `CodexAuthState`
- `CodexAuthStore`
- `CodexTokenManager`

Package entrypoint:

- [Package entrypoint](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/__init__.py)

## Module Guide

| Subsystem | Key files | Use them for |
| --- | --- | --- |
| top-level constructors and model surface | [factory module](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/factory.py), [model module](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/model.py), [Codex client module](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/client.py) | building public client/model objects and default request behavior |
| auth state and persistence | [auth-state module](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/auth/state.py), [auth-store module](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/auth/store.py), [auth-config module](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/auth/config.py) | parsing auth payloads, persistence, path config |
| token refresh | [token manager](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/auth/manager.py) | deciding whether refresh is needed and performing refresh |

## What It Does

It:

- reads an existing Codex auth file
- refreshes tokens when needed
- derives `ChatGPT-Account-Id`
- builds a Codex-specific Responses client
- returns a `pydantic-ai` model configured for Codex usage
- returns a LangChain `ChatOpenAI` configured for the Responses API

It does not:

- perform `codex login`
- create a login session from nothing
- support unrelated OpenAI Chat Completions flows
- adapt a runtime to ACP
- expose anything over WebSocket

## Common Integration Pattern

The most important package combinations are:

- `codex-auth-helper` + `pydantic-acp`
- `codex-auth-helper` + `langchain-acp`

Normal flow:

1. call `create_codex_responses_model(...)`
2. construct a `pydantic_ai.Agent`
3. expose that agent through `pydantic-acp`

This helper is usually upstream of the adapter, not a replacement for it.

## Common Workflows

### Build a Codex-backed `pydantic-ai` model

Use `create_codex_responses_model(...)`.

### Build a lower-level client first

Use `create_codex_async_openai(...)` when you need the transport/client object explicitly.

### Build a LangChain model

Use `create_codex_chat_openai(...)` when the upstream runtime is LangChain or LangGraph and you
want the Responses API path instead of hand-wiring `langchain-openai`.

### Debug refresh behavior

Start from the [token manager](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/auth/manager.py), then inspect the [auth-state module](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/auth/state.py) and the [auth-config module](https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/auth/config.py).

## Skill-Bundled Example

Skill-local example:

- [Codex-backed agent example](https://github.com/vcoderun/acpkit/blob/main/.agents/skills/codex-auth-helper/examples/codex_responses_agent.py)
- [Codex-backed LangChain graph example](https://github.com/vcoderun/acpkit/blob/main/.agents/skills/codex-auth-helper/examples/codex_chat_openai_graph.py)
- [Example notes](https://github.com/vcoderun/acpkit/blob/main/.agents/skills/codex-auth-helper/examples/README.md)

This example demonstrates:

- creating the Codex-backed model
- constructing a Pydantic agent
- exposing that agent through `pydantic-acp`
- constructing a LangChain graph with `ChatOpenAI`

## Handoff Rules

Pair or switch to:

- `pydantic-acp`
  when the Codex-backed model is being exposed through ACP
- `langchain-acp`
  when the Codex-backed LangChain graph is being exposed through ACP
- `acpkit-sdk`
  only when that Pydantic agent is invoked through `acpkit run ...` or `acpkit serve ...`

Stay in this skill when the main issue is:

- auth parsing
- refresh behavior
- client header behavior
- model construction

## Guardrails

- Do not call this package an adapter.
- Do not claim it supports generic Chat Completions if it does not.
- Do not describe auth refresh logic that is not present in the package source.
- If the task shifts into ACP adapter runtime behavior, move to `pydantic-acp`.
