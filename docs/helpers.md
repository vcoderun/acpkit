# Helpers

ACP Kit also ships helper packages that are useful around the adapter runtime but are not themselves adapter packages.

Today the main helper packages are:

- `codex-auth-helper`
- `acpremote`

Helper docs:

- [`acpremote Overview`](acpremote.md)
- [`codex-auth-helper` API Reference](api/codex_auth_helper.md)

## acpremote

`acpremote` is the transport helper package.

It handles:

- exposing any existing `acp.interfaces.Agent` over WebSocket
- exposing stdio ACP commands over WebSocket
- mirroring a remote ACP endpoint back into a local ACP agent boundary
- serving `/acp` metadata and `/healthz` alongside the WebSocket endpoint

Use it when you already have an ACP server and need remote transport, not when you need to adapt a framework runtime into ACP for the first time.

If the runtime is still a Python target, the usual path is:

1. resolve it through `acpkit`
2. expose it through `pydantic-acp` or `langchain-acp`
3. use `acpremote` only when you need WebSocket transport or a local mirror

Read the full transport guide in [acpremote Overview](acpremote.md).

## codex-auth-helper

`codex-auth-helper` turns an existing local Codex login into a `pydantic-ai` Responses model.

It handles:

- reading `~/.codex/auth.json`
- refreshing expired tokens
- deriving the account id
- constructing a Codex-specific `AsyncOpenAI` client
- returning a ready-to-use `CodexResponsesModel`

## Why It Exists

Codex-backed model usage is easy to get subtly wrong by hand.

The helper centralizes the backend-specific behavior that should stay stable:

- Codex Responses endpoint wiring
- auth refresh flow
- `openai_store=False`
- streamed Responses usage even when Pydantic AI takes a non-streaming request path

## Minimal Usage

```python
from codex_auth_helper import create_codex_responses_model
from pydantic_ai import Agent

model = create_codex_responses_model("gpt-5.4")
agent = Agent(model, instructions="You are a helpful coding assistant.")
```

ACP-side usage looks the same:

```python
from codex_auth_helper import create_codex_responses_model
from pydantic_ai import Agent
from pydantic_acp import run_acp

agent = Agent(
    create_codex_responses_model("gpt-5.4"),
    name="codex-agent",
)

run_acp(agent=agent)
```

## What It Does Not Do

- it does not log you into Codex
- it does not create `~/.codex/auth.json`
- it does not support Chat Completions style `OpenAIChatModel`
- it does not replace Pydantic AI itself

## Lower-level Factories

If you want more control, the helper also exposes:

- `create_codex_async_openai(...)`
- `CodexAsyncOpenAI`
- `CodexResponsesModel`
- `CodexAuthConfig`
- `CodexTokenManager`

The full API is documented in [API Reference](api/codex_auth_helper.md).
