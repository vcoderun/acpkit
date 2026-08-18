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

`codex-auth-helper` turns an existing local Codex login into either:

- a `pydantic-ai` Responses model
- a LangChain `ChatOpenAI` model pinned to the Responses API

It handles:

- reading `~/.codex/auth.json`
- refreshing expired tokens
- deriving the account id
- constructing Codex-specific OpenAI clients
- returning a ready-to-use `CodexResponsesModel`
- returning a ready-to-use LangChain `ChatOpenAI`

## Why It Exists

Codex-backed model usage is easy to get subtly wrong by hand.

The helper centralizes the backend-specific behavior that should stay stable:

- Codex Responses endpoint wiring
- auth refresh flow
- `openai_store=False`
- required SSE transport even when Pydantic AI takes a non-streaming request path
- real incremental deltas through Pydantic AI and LangChain streaming APIs

## Minimal Usage

Pydantic AI:

```python
from codex_auth_helper import create_codex_responses_model
from pydantic_ai import Agent

model = create_codex_responses_model(
    "gpt-5.4",
    instructions="You are a helpful coding assistant.",
)
agent = Agent(model)
```

Pass `instructions=` explicitly to `create_codex_responses_model(...)`. On the
Pydantic path you can also add `Agent(instructions=...)` when you want
agent-owned instructions on top of the factory default.

LangChain:

```python
from codex_auth_helper import create_codex_chat_openai
from langchain.agents import create_agent

graph = create_agent(
    model=create_codex_chat_openai(
        "gpt-5.4",
        instructions="You are a helpful coding assistant.",
    ),
    tools=[],
    name="codex-graph",
)
```

`create_codex_chat_openai(...)` requires `instructions=`. There is no implicit
default on the LangChain path.

## Streaming

The Pydantic model forwards Codex Responses deltas without buffering the full response:

```python
async with agent.run_stream("Summarize the current workspace.") as result:
    async for delta in result.stream_text(delta=True):
        print(delta, end="", flush=True)
```

The LangChain factory sets `streaming=True` by default and works with the normal `astream()` API:

```python
model = create_codex_chat_openai(
    "gpt-5.4",
    instructions="You are a concise coding assistant.",
)
async for chunk in model.astream("Summarize the current workspace."):
    print(chunk.text, end="", flush=True)
```

Set `streaming=False` only for LangChain consumers that require its non-streaming model path. The
Pydantic model still uses the Codex backend's required SSE transport for ordinary `request()` calls.

ACP-side usage looks the same:

```python
from codex_auth_helper import create_codex_responses_model
from pydantic_ai import Agent
from pydantic_acp import run_acp

agent = Agent(
    create_codex_responses_model(
        "gpt-5.4",
        instructions="You are a helpful ACP coding assistant.",
    ),
    name="codex-agent",
)

run_acp(agent=agent)
```

## What It Does Not Do

- it does not log you into Codex
- it does not create `~/.codex/auth.json`
- it does not provide generic Chat Completions wiring
- it does not replace Pydantic AI itself

## Lower-level Factories

If you want more control, the helper also exposes:

- `create_codex_async_openai(...)`
- `create_codex_openai(...)`
- `create_codex_chat_openai(...)`
- `CodexAsyncOpenAI`
- `CodexOpenAI`
- `CodexResponsesModel`
- `CodexAuthConfig`
- `CodexTokenManager`

The full API is documented in [API Reference](api/codex_auth_helper.md).
