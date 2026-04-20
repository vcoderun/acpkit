---
name: "codex-auth-helper"
description: "Use for `codex-auth-helper` tasks: reading local Codex auth state, refreshing tokens, building Codex-backed Responses clients/models, and wiring them into `pydantic-ai` or `pydantic-acp`."
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

## Start Here

If you only need the shortest high-signal path:

1. read `Quick Routing`
2. open `factory.py` for public constructor behavior
3. open `auth/manager.py` for refresh logic
4. open `auth/state.py` and `auth/store.py` for auth payload shape and persistence

## Quick Routing

| If the task is about... | Use this skill? | Open first |
| --- | --- | --- |
| `~/.codex/auth.json` parsing | Yes | `auth/state.py`, `auth/store.py` |
| token refresh timing or refresh requests | Yes | `auth/manager.py`, `auth/config.py` |
| Codex-specific request headers or account id | Yes | `client.py`, `auth/state.py` |
| building a `CodexResponsesModel` | Yes | `factory.py`, `model.py` |
| exposing that model through ACP | Pair with `pydantic-acp` | `packages/adapters/pydantic-acp/...` |
| WebSocket transport | No, pair with `acpremote` | `packages/transports/acpremote/...` |

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

- Raw skill:
  `https://raw.githubusercontent.com/vcoderun/acpkit/main/.agents/skills/codex-auth-helper/SKILL.md`
- Raw helpers docs:
  `https://raw.githubusercontent.com/vcoderun/acpkit/main/docs/helpers.md`
- Raw API docs page:
  `https://raw.githubusercontent.com/vcoderun/acpkit/main/docs/api/codex_auth_helper.md`
- Rendered helper docs:
  `https://vcoderun.github.io/acpkit/helpers/`
- Source tree:
  `https://github.com/vcoderun/acpkit/tree/main/packages/helpers/codex-auth-helper`

Cross-skill reference:

- Pydantic adapter skill:
  `https://raw.githubusercontent.com/vcoderun/acpkit/main/.agents/skills/pydantic-acp/SKILL.md`

## Public Surface

High-value public seams:

- `create_codex_responses_model(...)`
- `create_codex_async_openai(...)`
- `CodexAsyncOpenAI`
- `CodexResponsesModel`
- `CodexAuthConfig`
- `CodexAuthState`
- `CodexAuthStore`
- `CodexTokenManager`

Package entrypoint:

- `https://github.com/vcoderun/acpkit/blob/main/packages/helpers/codex-auth-helper/src/codex_auth_helper/__init__.py`

## Module Guide

| Subsystem | Key files | Use them for |
| --- | --- | --- |
| top-level constructors and model surface | `factory.py`, `model.py`, `client.py` | building public client/model objects and default request behavior |
| auth state and persistence | `auth/state.py`, `auth/store.py`, `auth/config.py` | parsing auth payloads, persistence, path config |
| token refresh | `auth/manager.py` | deciding whether refresh is needed and performing refresh |

## What It Does

It:

- reads an existing Codex auth file
- refreshes tokens when needed
- derives `ChatGPT-Account-Id`
- builds a Codex-specific Responses client
- returns a `pydantic-ai` model configured for Codex usage

It does not:

- perform `codex login`
- create a login session from nothing
- support unrelated OpenAI Chat Completions flows
- adapt a runtime to ACP
- expose anything over WebSocket

## Common Integration Pattern

The most important package combination is:

- `codex-auth-helper` + `pydantic-acp`

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

### Debug refresh behavior

Start from `auth/manager.py`, then inspect `auth/state.py` and `auth/config.py`.

## Skill-Bundled Example

Skill-local example:

- `https://github.com/vcoderun/acpkit/blob/main/.agents/skills/codex-auth-helper/examples/codex_responses_agent.py`
- `https://github.com/vcoderun/acpkit/blob/main/.agents/skills/codex-auth-helper/examples/README.md`

This example demonstrates:

- creating the Codex-backed model
- constructing a Pydantic agent
- exposing that agent through `pydantic-acp`

## Handoff Rules

Pair or switch to:

- `pydantic-acp`
  when the Codex-backed model is being exposed through ACP
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
