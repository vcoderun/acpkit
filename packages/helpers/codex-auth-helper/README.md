# codex-auth-helper

`codex-auth-helper` turns an existing local Codex auth session into either:

- a `pydantic-ai` Responses model
- a LangChain `ChatOpenAI` model pinned to the OpenAI Responses API

It reads `~/.codex/auth.json`, refreshes access tokens when needed, builds
Codex-specific OpenAI clients for the Responses endpoint, and returns either a
ready-to-use `CodexResponsesModel` or a LangChain chat model.

## What It Does

- Reads tokens from `~/.codex/auth.json`
- Derives `ChatGPT-Account-Id` from the auth file or token claims
- Refreshes expired access tokens with `https://auth.openai.com/oauth/token`
- Writes refreshed tokens back to the auth file with private, atomic file replacement
- Builds an OpenAI-compatible client pointed at `https://chatgpt.com/backend-api/codex`
- Returns a `pydantic-ai` responses model that already applies the Codex backend requirements
- Returns a LangChain `ChatOpenAI` model configured for the Responses API

The helper enforces two backend-specific behaviors for you:

- `openai_store=False`
- an SSE Responses request even when `pydantic-ai` calls the non-streamed `request()` path
- incremental delta delivery when callers use the Pydantic AI or LangChain streaming APIs

It also provides an explicit Responses Lite transport for models and Codex
routes that require that wire format. The normal Responses transport remains
the default.

## What It Does Not Do

- It does not log you into Codex
- It does not create `~/.codex/auth.json`
- It does not provide generic Chat Completions wiring
- It does not replace `pydantic-ai`; it only provides a model/client factory

## Install

For the latest stable release:

```bash
uv add codex-auth-helper
```

```bash
pip install codex-auth-helper
```

For LangChain usage:

```bash
uv add "codex-auth-helper[langchain]"
```

```bash
pip install "codex-auth-helper[langchain]"
```

For the optional Responses WebSocket connection (Pydantic AI or LangChain):

```bash
uv add "codex-auth-helper[websocket]"
```

```bash
pip install "codex-auth-helper[websocket]"
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

model = create_codex_responses_model(
    "gpt-5.4",
    instructions="You are a helpful coding assistant.",
)
agent = Agent(model)

result = agent.run_sync("Naber")
print(result.output)
```

## LangChain Quick Start

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

The LangChain helper returns a `langchain_openai.ChatOpenAI` subclass configured to:

- use the Codex Responses endpoint
- reuse local Codex auth state
- keep `use_responses_api=True`
- default to `output_version="responses/v1"`
- require `instructions=` and pass it through to the Responses request
- default to `streaming=True` for incremental LangChain delivery
- stream Responses internally, including non-streaming invocation APIs

Active LangChain system messages replace the factory's fallback instructions in
the Codex `instructions` field. Native tool/schema/event conversion is retained.

`instructions` is mandatory for `create_codex_chat_openai(...)`. The helper does not provide an
implicit system prompt for the LangChain path; callers must pass the behavior they want explicitly.

The same rule applies to `create_codex_responses_model(...)` on the Pydantic path. Pass the Codex
system behavior to the helper directly instead of relying on a separate agent-level instruction just
to seed the model.

## Streaming

`CodexResponsesModel.request_stream()` forwards Responses API deltas as they arrive; it does not
wait for the completed response before yielding text. Consume it through the ordinary Pydantic AI
agent streaming surface:

```python
import asyncio

from codex_auth_helper import create_codex_responses_model
from pydantic_ai import Agent


async def main() -> None:
    agent = Agent(
        create_codex_responses_model(
            "gpt-5.4",
            instructions="You are a concise coding assistant.",
        )
    )
    async with agent.run_stream("Explain this repository in three sentences.") as result:
        async for delta in result.stream_text(delta=True):
            print(delta, end="", flush=True)


asyncio.run(main())
```

The LangChain factory enables model streaming by default. `astream()` therefore yields
`AIMessageChunk` values incrementally:

```python
import asyncio

from codex_auth_helper import create_codex_chat_openai


async def main() -> None:
    model = create_codex_chat_openai(
        "gpt-5.4",
        instructions="You are a concise coding assistant.",
    )
    async for chunk in model.astream("Explain this repository in three sentences."):
        print(chunk.text, end="", flush=True)


asyncio.run(main())
```

Pass `streaming=False` to `create_codex_chat_openai(...)` only when a LangChain consumer explicitly
requires the non-streaming model path. This option does not disable the Codex backend's required SSE
transport for Pydantic AI `request()` calls.

Pydantic factory fallback instructions are carried on request parameters, not
inserted as synthetic history messages. Explicit active instructions take precedence.

## Custom Auth Path

If you want to read a different auth file, pass a custom config:

```python
from pathlib import Path

from codex_auth_helper import CodexAuthConfig, create_codex_responses_model

config = CodexAuthConfig(auth_path=Path("/tmp/codex-auth.json"))
model = create_codex_responses_model(
    "gpt-5.4",
    config=config,
    instructions="You are a helpful coding assistant.",
)
```

## Auth State Safety

The auth state file contains credentials and should be treated as private host state.

When refreshed tokens are written back, `CodexAuthStore` uses a private temp file, `fsync`, atomic
replace, and POSIX `0600` permissions for the final file. If replace fails, the previous auth file is
left intact and the temp file is cleaned up.

Keep the parent directory private and do not copy auth state into logs, examples, test fixtures, or
container images.

## Passing Extra OpenAI Responses Settings

Additional `OpenAIResponsesModelSettings` can still be passed through. The helper
keeps `openai_store=False` unless you explicitly override the model after
construction.

```python
from codex_auth_helper import create_codex_responses_model

model = create_codex_responses_model(
    "gpt-5.4",
    instructions="You are a helpful coding assistant.",
    settings={
        "openai_reasoning_summary": "concise",
    },
)
```

## Responses Lite

Select the Codex Responses Lite wire format explicitly:

```python
from codex_auth_helper import create_codex_responses_model

model = create_codex_responses_model(
    "gpt-5.6-luna",
    instructions="You are a helpful coding assistant.",
    transport="responses_lite",
)
```

The helper applies the Lite request contract at send time. It moves base
instructions and tool definitions into stable input items, requests all-turn
reasoning context, and forces `parallel_tool_calls=False` as required by the
Codex backend.

Responses Lite does not currently support native top-level parallel tool-call
batches. Use the default `transport="responses"` when native parallel tool
calling is required. Concurrency performed inside a separate code-execution
tool is independent of this API-level restriction.

Lite support is model- and route-dependent. The helper does not silently fall
back to the normal Responses transport when the backend rejects a model.

## Client Identity And Routing Headers

Pydantic AI, LangChain, and the lower-level clients use the helper's own identity:

- `originator: codex-auth-helper`
- `User-Agent: codex-auth-helper/<installed version>`
- `x-codex-routing-hint: model=<effective model>`, with `;tier=<effective tier>`
  when a service tier is explicitly supplied.

WebSocket connections also send
`OpenAI-Beta: responses_websockets=2026-02-06`. HTTP does not add this beta header.
Explicit caller-provided header values are preserved case-insensitively. The
helper does not identify itself as Codex CLI or another agent implementation.

Changing the generated routing hint replaces an open WebSocket connection; the
native models then send full history. Identical connection options keep the
connection reusable. These headers do not guarantee provider cache hits.

## Responses WebSocket

The Pydantic AI and LangChain models can keep one Responses WebSocket open for a logical run
and continue append-only turns with `previous_response_id`:

```python
import asyncio

from codex_auth_helper import create_codex_responses_model
from pydantic_ai import Agent


async def main() -> None:
    model = create_codex_responses_model(
        "gpt-5.6-luna",
        instructions="You are a helpful coding assistant.",
        connection="websocket",
        fallback="error",
    )
    agent = Agent(model)
    async with model.responses_turn():
        result = await agent.run("Inspect the repository and report the result.")
    print(result.output)


asyncio.run(main())
```

The first request sends full input. A later turn sends only the new suffix when
the acknowledged history is an exact prefix and the effective request contract
is unchanged. Instructions are sent on every continuation. An edited history,
changed tools/schema/settings, incomplete response, cancellation, or early
stream close invalidates continuation and forces the next request to start from
full input.

Stopping consumption after `response.completed` preserves the completed response
and the connection. Stopping before completion still abandons the response.
If the socket is already known to be closed before the next model request, the
model opens a new connection and sends full history. Changing handshake headers
also replaces the connection; unchanged options keep it reusable. Lower-level
client calls honor changes to both `extra_headers` and `extra_query`, but must
supply full input themselves when a connection needs replacement.

Non-streaming Pydantic model requests and LangChain `ainvoke` also recover from
transient send/receive failures. They discard the incomplete response, reconnect,
and rebuild the full native history without its stale response ID. Recovery uses
the underlying client's `max_retries` (default two reconnects), with bounded
backoff starting at 250 ms. It does not rerun the agent or completed local tools.
The recovered model can still choose to request a tool again; this is not an
exactly-once execution guarantee.

With `fallback="http"`, exhausting those WebSocket retries permits one transition
to HTTP for that session. Initial handshake fallback remains supported. With
`fallback="error"`, exhausted retries raise instead. HTTP retains the SDK's retry
policy. A lost response may already have consumed provider tokens; recovery is
not a billing rollback and cannot account for unreported usage.

Public streams and raw client calls are not replayed after a failed send or
interrupted response. Cancellation, permanent auth/permission failures and
requests containing provider-hosted tools are not response-retry candidates.
Incomplete protocol/error responses are not treated as successful output.
Responses Lite remains HTTP-only; HTTP remains the default connection mode.

The response-recovery behavior is available from `codex-auth-helper` 1.8.0.

For LangChain, use `create_codex_chat_openai(..., connection="websocket")` and
keep `async with model.responses_session():` around a complete graph run or
several sequential calls with full native message history. The model uses
acknowledged response IDs only when the native input prefix and request contract
remain unchanged. Edited history, changed tools/schema/instructions/settings and
renewed sockets send full input. Cancellation and early stream close invalidate
the chain. Do not enable `use_previous_response_id` or pass response IDs manually.

Native synchronous LangChain model calls and `include_response_headers=True`
are unavailable over WebSocket; use `ainvoke`/`astream`. Kedi's synchronous
adapter wrappers run the asynchronous lifecycle. HTTP remains available for
native synchronous model calls. Install the optional `langchain` dependency;
importing the package for Pydantic AI does not import LangChain.

The LangChain factory leaves native streaming selection automatic for WebSocket:
ordinary `ainvoke` buffers the response for recovery, while `astream` streams it.
Explicit `streaming=True` or streaming callbacks can select the exposed-stream
path even for `ainvoke`; that path does not replay partial output. HTTP's default
streaming setting is unchanged. Buffered callbacks receive only successful output.

Direct Pydantic AI callers should keep the explicit `responses_session()`
around the whole agent run. Without it, an individual request still works, but
the connection and continuation state are scoped to that request. Frameworks
such as Kedi can own this context at their logical run boundary.

The closing handshake has a 250 ms timeout so an unresponsive peer cannot hold
up run cleanup for several seconds. This is a shutdown timeout, not a limit on
model generation or tool execution.

Continuation compares value snapshots of request-relevant history, including
the last completed model output. In-place edits therefore send full history
instead of silently continuing from stale server context. Values that cannot
be snapshotted disable this optimization without blocking normal generation.
A shared session rejects another submission while a connection is being opened
or a request is being sent, as well as while its response is streaming.

For native Pydantic AI, the explicit model session can span several sequential
agent calls when each call supplies the appropriate message history. Kedi's
`PydanticAdapter` provides its own `adapter.responses_session()` context for
this ownership boundary, while preserving child-task isolation. Closing and
reopening a session never carries a stale response ID onto a new connection.

For measurements, pass `transport_observer=` to the factory. It receives
bounded `CodexResponsesTransportEvent` records containing connection mode,
full/delta decisions, item/byte counts, and lifecycle status. Events never
contain prompts, tool payloads, auth headers, account IDs, or raw response IDs.
Observer exceptions are counted on `CodexResponsesSessionInfo` and do not alter
model execution; callbacks should still return quickly.
Receive failures emit `failed` with category `receive`; recovery emits `retry`
or `fallback`. This distinguishes lost attempts from completed model responses.

## User Turns And Auth Recovery

`responses_session()` owns a reusable connection and its continuation chain.
`responses_turn()` scopes routing state to one user request and all its tool
round-trips. Both Pydantic and LangChain models expose the async turn context;
it also opens a session when one is not already active:

```python
async with model.responses_session():
    async with model.responses_turn():
        first = await agent.run("Inspect the deployment record.")
    async with model.responses_turn():
        second = await agent.run("Check the rollback.", message_history=first.all_messages())
```

For LangChain, put the complete `graph.ainvoke(...)` or `graph.astream(...)`
inside each turn context. The async lower-level client also exposes
`responses_turn()`; the synchronous client uses `with client.responses_turn():`.
Kedi scopes turns automatically at each Pydantic/LangChain agent-run boundary.
Native helper callers opt in explicitly; an unscoped request does not retain
server turn state.

The first valid `x-codex-turn-state` from an HTTP response, WebSocket handshake,
or `codex.response.metadata` event is echoed unchanged on subsequent requests
within that turn. HTTP uses the header; WebSocket uses `client_metadata`, and
a replacement handshake receives the same active state. New or nested turns
start fresh. Exit, failure, and cancellation discard their routing state, even
if the connection remains open for later turns. The value is not stored in
conversation history, transport metrics, or credential files. It is independent
of `prompt_cache_key` and does not guarantee a cache hit. Within a managed turn,
the helper owns this header slot; other explicit identity headers still win.

Before refreshing expired credentials, the manager reloads the auth file and
accepts an already-refreshed token only for the same account. Independent managers
for the same file share one in-process refresh lock. Access-token expiry takes
precedence over ID-token expiry; an old ID token alone does not trigger refresh.

An explicit Responses HTTP 401 or WebSocket handshake 401 has bounded recovery:
reload the file, retry if the credential changed, then refresh once if rejection
continues. Custom HTTP Authorization headers and other endpoints are excluded.
An exhausted 401 or permanent auth error does not trigger WebSocket-to-HTTP
fallback. Accepted requests, uncertain sends, and interrupted streams are not
replayed by auth recovery.

`CodexAuthAccountMismatchError` requires a new client after switching accounts.
`CodexAuthRefreshError.reason` identifies `expired`, `reused`, `invalidated`, or
`rejected` refresh credentials without including upstream error bodies. Sign in
again when necessary. File writes remain atomic, and a detected newer login is
not overwritten, but refresh serialization is **in-process**, not a cross-process
lock or a guarantee against all concurrent external credential changes.

## Lower-Level Client Factory

If you only want the authenticated OpenAI client, use `create_codex_async_openai(...)`:

```python
from codex_auth_helper import create_codex_async_openai

client = create_codex_async_openai()
```

This returns `CodexAsyncOpenAI`, a subclass of `openai.AsyncOpenAI`.

On OpenAI SDK 3.x, a custom `http_client=` must use the SDK's transport type,
such as `openai.DefaultAsyncHttpxClient`. Token refresh deliberately uses a
separate standard `httpx.AsyncClient`, configurable with `auth_http_client=`.
The split prevents SDK transport upgrades from changing auth persistence or
refresh behavior.

If you need the sync OpenAI client, use `create_codex_openai(...)`.

## Public API

```python
from codex_auth_helper import (
    CodexAsyncOpenAI,
    CodexAuthConfig,
    CodexAuthState,
    CodexOpenAI,
    CodexAuthStore,
    CodexResponsesModel,
    CodexResponsesConnection,
    CodexResponsesConnectionError,
    CodexResponsesFallback,
    CodexResponsesProtocolError,
    CodexResponsesSessionInfo,
    CodexResponsesTransportEvent,
    CodexResponsesTransportObserver,
    CodexResponsesTransport,
    CodexTokenManager,
    create_codex_async_openai,
    create_codex_chat_openai,
    create_codex_openai,
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
- private, atomic auth state writes
- Codex-specific OpenAI client wiring
- `pydantic-ai` responses model factory
- LangChain Responses-model factory

## Documentation

- [Helpers Overview](https://vcoderun.github.io/acpkit/helpers/)
- [API Reference](https://vcoderun.github.io/acpkit/api/codex_auth_helper/)
- [Security Guidance](https://vcoderun.github.io/acpkit/security/)
