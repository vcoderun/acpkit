# `codex_auth_helper` API

`codex-auth-helper` is intentionally small. The public API is documented here so ACP examples can depend on it without requiring readers to inspect the package source first.

## Functions

::: codex_auth_helper.create_codex_responses_model

::: codex_auth_helper.create_codex_async_openai

::: codex_auth_helper.create_codex_openai

::: codex_auth_helper.create_codex_chat_openai

## Classes

::: codex_auth_helper.CodexResponsesModel

::: codex_auth_helper.CodexAsyncOpenAI

::: codex_auth_helper.CodexOpenAI

::: codex_auth_helper.CodexResponsesSessionInfo

::: codex_auth_helper.CodexResponsesTransportEvent

::: codex_auth_helper.CodexResponsesConnectionError

::: codex_auth_helper.CodexResponsesProtocolError

::: codex_auth_helper.CodexAuthConfig

::: codex_auth_helper.CodexAuthState

::: codex_auth_helper.CodexAuthStore

::: codex_auth_helper.CodexTokenManager

::: codex_auth_helper.CodexAuthAccountMismatchError

::: codex_auth_helper.CodexAuthRefreshError

## Turn Lifecycle

`async with model.responses_turn():` scopes one user request, including its tool
loop, for both Pydantic AI and LangChain. It opens a Responses session if needed.
Wrap several separate turns in `model.responses_session()` to reuse a connection;
each turn still starts with fresh routing state. Kedi owns this run boundary
automatically. The lower-level async and sync clients expose equivalent async
and sync `responses_turn()` contexts.

The first server `x-codex-turn-state` is retained only until turn exit and echoed
unchanged. HTTP reads response headers. WebSocket reads handshake headers and
`codex.response.metadata`, then uses `response.create.client_metadata` and any
replacement handshake. The managed slot overrides caller-supplied turn state,
without changing other metadata. It never becomes prompt history or a metric
value; cancellation and nested scopes cannot carry it into a new turn. This is
routing affinity, not a provider cache-hit guarantee.

## Credential Recovery

Expired credentials are reloaded before refresh. Same-file managers serialize
refreshes inside the process and reject account changes. An explicit Responses
HTTP 401 or handshake 401 may reload once, then refresh once; custom HTTP
Authorization and unrelated endpoints are not intercepted. Permanent auth errors
and exhausted 401s do not switch WebSocket sessions to HTTP. Recovery does not
replay an accepted response or an ambiguous send/stream failure.

Refresh failures expose a safe `CodexAuthRefreshError.reason` (`expired`, `reused`,
`invalidated`, or `rejected`), not raw server error bodies. Account changes raise
`CodexAuthAccountMismatchError`. Create a new client after changing login.
Access-token expiry is authoritative when available. Atomic file writes and a
pre-write rotation check remain, but there is no cross-process refresh lock.

## Request Headers

The native Pydantic AI and LangChain factories and both lower-level clients send
`originator: codex-auth-helper` and
`User-Agent: codex-auth-helper/<installed version>`. Effective Responses model
and tier settings determine `x-codex-routing-hint`; `extra_body` overrides are
included when deriving that hint. An explicitly supplied service tier adds
`;tier=<effective tier>`; an omitted tier does not add a tier hint.

WebSocket handshakes additionally send
`OpenAI-Beta: responses_websockets=2026-02-06`. The HTTP path does not add it.
Caller-provided values take precedence regardless of header-name casing, and
the caller's header mapping is not mutated. Changed handshake options replace
the connection before dispatch; native model integrations resume with full
history instead of reusing the old connection's response ID.

These defaults identify this package, not Codex CLI or another client. They are
not a guarantee of cache affinity, cache hits, or future backend acceptance.

## Types

`CodexResponsesTransport` is the public literal transport selector:

- `"responses"` keeps the standard Responses request shape and is the default.
- `"responses_lite"` emits the Codex Responses Lite request shape and disables
  native parallel tool-call batches as required by that backend contract.

`CodexResponsesConnection` selects the Pydantic model's outbound Responses
connection:

- `"http"` preserves the existing default request path.
- `"websocket"` keeps one connection for an explicit logical run and enables
  append-only continuation with `previous_response_id`.

WebSocket shutdown allows 250 ms for the closing handshake. This deadline does
not limit response generation or tool execution.

History comparisons use independent snapshots of request-relevant values,
including completed model output. Editing messages in place resets continuation;
uncopyable values use the full-request path. Usage, run IDs, and other local run
bookkeeping do not invalidate an otherwise unchanged prefix. Concurrent requests
to one session are rejected before handshake or send can overlap. Separate
sessions remain independent.

Transport metrics are computed only when `transport_observer` is configured;
an absent observer does not trigger payload-size serialization.

`CodexResponsesFallback` controls initial WebSocket handshake failure:

- `"error"` is strict and is the default.
- `"http"` permits a recorded fallback before any request is submitted. It does
  not replay ambiguous send or stream failures.

`CodexResponsesTransportObserver` is a synchronous callback receiving
content-free `CodexResponsesTransportEvent` values. Keep callbacks non-blocking;
exceptions are counted on the active `CodexResponsesSessionInfo` and isolated
from request execution.
