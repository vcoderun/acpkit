# Typed Elicitation

`AcpSessionContext.ask_choice()` asks the connected ACP client to select one
typed application value. It is an ergonomic layer over
`AcpSessionContext.create_elicitation()`; the low-level API remains available
for custom ACP form and URL elicitation.

The helper does not introduce another schema system. It compiles choices to
ACP's existing `ElicitationFormSessionMode`, `ElicitationSchema`, and
`ElicitationStringPropertySchema` types.

## Single-Choice Questions

Each `ElicitationChoice` carries an application value, a user-visible label,
an optional description, and whether it is the default:

```python
from pydantic_acp import (
    AcpSessionContext,
    ChoiceElicitationAccepted,
    ChoiceElicitationCancelled,
    ChoiceElicitationDeclined,
    ElicitationChoice,
)


async def choose_target(session: AcpSessionContext) -> str:
    result = await session.ask_choice(
        "Choose a deployment target",
        [
            ElicitationChoice(value="preview", label="Preview"),
            ElicitationChoice(
                value="production",
                label="Production",
                description="Deploy to the production environment.",
                default=True,
            ),
        ],
    )

    if isinstance(result, ChoiceElicitationAccepted):
        return f"Selected: {result.value}"
    if isinstance(result, ChoiceElicitationDeclined):
        return "The user declined to choose."
    if isinstance(result, ChoiceElicitationCancelled):
        return "The choice was cancelled."
    raise AssertionError("unreachable")
```

The value is not sent as the ACP enum constant. ACP Kit sends opaque string
tokens and maps the accepted token back to the original typed value. Values
can therefore be enums, dataclasses, identifiers, or other application types;
they do not need to be JSON serializable.

Exactly zero or one choice may have `default=True`. A default is transmitted
through the ACP property schema. It does not turn a malformed accepted response
into a valid answer: an accepted response must still contain one of the offered
tokens.

## Result Contract

The result is a discriminated union. Inspect `result.status` or narrow with the
public result classes:

| Status | Type | Meaning |
| --- | --- | --- |
| `accepted` | `ChoiceElicitationAccepted[T]` | The user selected a choice; `.value` contains the typed value. |
| `declined` | `ChoiceElicitationDeclined` | The user explicitly declined to answer. |
| `cancelled` | `ChoiceElicitationCancelled` | The interaction was cancelled without an answer. |

`None` is never used to collapse these outcomes. If a client returns an
accepted response without a known choice token, ACP Kit raises
`InvalidElicitationResponseError`.

## Capability Negotiation And Fallbacks

`ask_choice()` always checks `session.supports_elicitation(mode)` before
sending the request. It never substitutes ACP permission requests for
elicitation. Without form support it raises `ElicitationUnsupportedError`:

```python
from pydantic_acp import ElicitationChoice, ElicitationUnsupportedError

try:
    result = await session.ask_choice(
        "Continue?",
        [ElicitationChoice(value=True, label="Continue")],
    )
except ElicitationUnsupportedError:
    result = None
```

When the application has a deliberate non-ACP fallback, pass a synchronous or
asynchronous callable that returns the same result union. The fallback runs
only when the client lacks form elicitation, and its owner must state whether
the outcome is accepted, declined, or cancelled:

```python
from pydantic_acp import ChoiceElicitationDeclined


async def unsupported_client_fallback() -> ChoiceElicitationDeclined:
    return ChoiceElicitationDeclined()


result = await session.ask_choice(
    "Choose a deployment target",
    [
        ElicitationChoice(value="preview", label="Preview", default=True),
        ElicitationChoice(value="production", label="Production"),
    ],
    fallback=unsupported_client_fallback,
)
```

Return `ChoiceElicitationAccepted(value=...)` from a fallback only when the
fallback itself has an acceptance source whose semantics your application is
willing to treat as accepted. ACP Kit does not manufacture acceptance from a
decline, cancellation, or exception. Its value must match an offered choice;
ACP Kit returns the canonical typed value from that choice and raises
`InvalidElicitationFallbackError` for an impossible selection.

Returning a plain choice value remains temporarily supported for 1.6.0
compatibility, but emits `DeprecationWarning`. It is subject to the same
offered-choice validation. Prefer an
explicit result variant: plain-value fallback cannot communicate whether the
value came from a user, policy, or configuration. `None` is treated as a real
choice value when it was offered; it is never a decline/cancel sentinel.

## Presentation Contract

ACP Kit transmits the question, option labels, schema default, and typed-choice
semantics. The client owns visual layout and interaction design. Option
descriptions are sent as namespaced option metadata because the published ACP
Python SDK 0.11 does not expose its standard per-option description field;
clients may ignore that metadata.

The helper intentionally provides no guarantee about radio buttons, menus,
dialogs, ordering beyond the schema order, or whether descriptions are visible.

## Remote Transport

ACP Python SDK 0.11 keeps the receiving `elicitation/create` route behind its
unstable-protocol opt-in. Enable the flag on the ACP client connection that
receives the request from the upstream agent:

```python
from acp import run_agent
from acpremote import TransportOptions, connect_acp

proxy_agent = connect_acp(
    websocket_url,
    options=TransportOptions(use_unstable_protocol=True),
)
await run_agent(proxy_agent)
```

The upstream/sending agent can use plain `run_agent(acp_agent)`: an agent-side
unstable flag is not required merely to call `create_elicitation()`. A final
ACP client connected to the local proxy must likewise support form elicitation
and register its receiving unstable route. Route registration and capability
negotiation are independent requirements.

The public mirror CLIs expose the same opt-in:

```bash
acpkit run --addr ws://remote.example.com:8080/acp/ws --unstable-protocol
acpremote mirror ws://remote.example.com:8080/acp/ws --unstable-protocol
```

For `serve_command()`, the relay transports JSON-RPC frames unchanged. The
receiving ACP client must register the unstable route; the spawned command does
not need an agent-side flag merely to send elicitation.

## Low-Level Elicitation

Use `create_elicitation()` directly for multi-field forms, URL elicitation, or
schema shapes other than one typed choice. Check `supports_elicitation(mode)`
first and construct the ACP SDK mode explicitly. `ask_choice()` is deliberately
single-select; it does not infer multi-select behavior from collection values.
