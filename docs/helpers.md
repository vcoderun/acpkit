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
- streamed Responses usage even when Pydantic AI takes a non-streaming request path
- real incremental deltas through Pydantic AI and LangChain streaming APIs
- explicit Responses Lite request assembly when selected

OpenAI SDK transport clients and auth-refresh clients have separate ownership.
For OpenAI SDK 3.x, pass `openai.DefaultAsyncHttpxClient` as `http_client=` and
a standard `httpx.AsyncClient` as `auth_http_client=` when customization is
needed. Closing an owned helper client closes both resources; borrowed clients
remain caller-owned.

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

### Responses Lite

Responses Lite is an explicit transport choice; normal Responses remains the
default:

```python
model = create_codex_responses_model(
    "gpt-5.6-luna",
    instructions="You are a helpful coding assistant.",
    transport="responses_lite",
)
```

At request time the helper emits the Lite input-item form for instructions and
tools, requests all-turn reasoning context, and forces
`parallel_tool_calls=False`. The current Codex Lite backend does not accept
native top-level parallel tool-call batches. Keep the default
`transport="responses"` when that form of parallelism is required.

The helper does not automatically select Lite by model name and does not fall
back when the backend rejects a model/transport combination.

### Responses WebSocket

Install the optional connection dependency with
`uv add "codex-auth-helper[websocket]"`, then select it explicitly on the
Pydantic AI factory:

```python
model = create_codex_responses_model(
    "gpt-5.6-luna",
    instructions="You are a helpful coding assistant.",
    connection="websocket",
    fallback="error",
)

async with model.responses_session():
    result = await Agent(model).run("Complete the task.")
```

One session owns one outbound Responses connection. The first request is full;
append-only turns may use `previous_response_id` and transmit only their new
suffix. The helper resends instructions and resets to full input whenever
history or the effective request contract changes.

A stream consumed through `response.completed` retains its completed response
even if the caller stops iteration on that event. A socket already known to be
closed before the next model request is replaced with full-history input; changed
handshake headers also require a new connection. Lower-level client calls must
provide full input instead of a previous-response suffix in those cases.

`fallback="http"` is limited to an initial handshake failure. A failed send,
interrupted stream, cancellation, or incomplete response is never replayed.
Responses Lite remains HTTP-only. The LangChain factory supports
`connection="websocket"`, `fallback=`, and `transport_observer=` through a
`ChatOpenAI` subclass. Use `async with model.responses_session():` around the
complete async agent/graph run. It retains native message/tool/schema conversion
and continues only unchanged, acknowledged history on its owning connection.
System messages become Codex instructions; HTTP invocation streams internally.
Native synchronous LangChain calls and HTTP response-header capture require
the HTTP connection. Do not supply response IDs or enable unchecked native
`use_previous_response_id`. Kedi owns isolated sessions at adapter-run boundaries.

An optional `transport_observer=` receives content-free metadata for submitted,
completed, abandoned, failed, and fallback attempts. It reports physical input
item/byte counts and continuation decisions without exposing request content,
credentials, account IDs, or raw response IDs. Observer failures are isolated
from model execution.

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
- `CodexResponsesTransport`
- `CodexAuthConfig`
- `CodexTokenManager`

The full API is documented in [API Reference](api/codex_auth_helper.md).
