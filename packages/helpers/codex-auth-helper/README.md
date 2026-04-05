# codex-auth-helper

`codex-auth-helper` turns an existing local Codex auth session into a
`pydantic-ai` model.

It reads `~/.codex/auth.json`, refreshes access tokens when needed, builds a
custom `AsyncOpenAI` client for the Codex Responses endpoint, and returns a
ready-to-use `CodexResponsesModel`.

## What It Does

- Reads tokens from `~/.codex/auth.json`
- Derives `ChatGPT-Account-Id` from the auth file or token claims
- Refreshes expired access tokens with `https://auth.openai.com/oauth/token`
- Writes refreshed tokens back to the auth file
- Builds an OpenAI-compatible client pointed at `https://chatgpt.com/backend-api/codex`
- Returns a `pydantic-ai` responses model that already applies the Codex backend requirements

The helper enforces two backend-specific behaviors for you:

- `openai_store=False`
- streamed responses even when `pydantic-ai` calls the non-streamed `request()` path

## What It Does Not Do

- It does not log you into Codex
- It does not create `~/.codex/auth.json`
- It does not support `pydantic_ai.models.openai.OpenAIChatModel` or Chat Completions flows
- It does not replace `pydantic-ai`; it only provides a model/client factory

## Install

```bash
uv pip install codex-auth-helper
```

You also need an existing Codex auth session on the same machine:

```text
~/.codex/auth.json
```

If you have not logged in yet:

```bash
codex login
```

## Quick Start

```python
from codex_auth_helper import create_codex_responses_model
from pydantic_ai import Agent

model = create_codex_responses_model("gpt-5")
agent = Agent(model, instructions="You are a helpful coding assistant.")

result = agent.run_sync("Naber")
print(result.output)
```

## Custom Auth Path

If you want to read a different auth file, pass a custom config:

```python
from pathlib import Path

from codex_auth_helper import CodexAuthConfig, create_codex_responses_model

config = CodexAuthConfig(auth_path=Path("/tmp/codex-auth.json"))
model = create_codex_responses_model("gpt-5", config=config)
```

## Passing Extra OpenAI Responses Settings

Additional `OpenAIResponsesModelSettings` can still be passed through. The helper
keeps `openai_store=False` unless you explicitly override the model after
construction.

```python
from codex_auth_helper import create_codex_responses_model

model = create_codex_responses_model(
    "gpt-5",
    settings={
        "openai_reasoning_summary": "concise",
    },
)
```

## Lower-Level Client Factory

If you only want the authenticated OpenAI client, use `create_codex_async_openai(...)`:

```python
from codex_auth_helper import create_codex_async_openai

client = create_codex_async_openai()
```

This returns `CodexAsyncOpenAI`, a subclass of `openai.AsyncOpenAI`.

## Public API

```python
from codex_auth_helper import (
    CodexAsyncOpenAI,
    CodexAuthConfig,
    CodexAuthState,
    CodexAuthStore,
    CodexResponsesModel,
    CodexTokenManager,
    create_codex_async_openai,
    create_codex_responses_model,
)
```

## Errors

Typical failure modes:

- `Codex auth file was not found ...`
  The machine is not logged into Codex yet.
- `Codex auth file ... does not contain valid JSON`
  The auth file is corrupt or partially written.
- `ModelHTTPError ... Store must be set to false`
  Means you are not using the helper-backed model instance.
- `ModelHTTPError ... Stream must be set to true`
  Means you are not using `CodexResponsesModel`.

## Package Notes

This package is intentionally small and focused:

- auth file parsing
- token refresh
- Codex-specific OpenAI client wiring
- `pydantic-ai` responses model factory
