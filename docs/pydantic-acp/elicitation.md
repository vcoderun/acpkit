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
asynchronous callable. Its typed value is returned as an accepted result, and
the fallback runs only when the client lacks form elicitation:

```python
async def configured_default() -> str:
    return "preview"


result = await session.ask_choice(
    "Choose a deployment target",
    [
        ElicitationChoice(value="preview", label="Preview", default=True),
        ElicitationChoice(value="production", label="Production"),
    ],
    fallback=configured_default,
)
```

Use a fallback only when proceeding without user input is valid. Do not use it
to pretend an unsupported client collected consent.

## Presentation Contract

ACP Kit transmits the question, option labels, schema default, and typed-choice
semantics. The client owns visual layout and interaction design. Option
descriptions are sent as namespaced option metadata because ACP 0.11 has no
standard per-option description field; clients may ignore that metadata.

The helper intentionally provides no guarantee about radio buttons, menus,
dialogs, ordering beyond the schema order, or whether descriptions are visible.

## Remote Transport

ACP 0.11 marks elicitation routes as unstable. Enable them explicitly on both
ends of an `acpremote` object connection:

```python
from acpremote import TransportOptions, connect_remote_agent, serve_acp

options = TransportOptions(use_unstable_protocol=True)
server = await serve_acp(acp_agent, options=options)
remote = await connect_remote_agent(client, websocket_url, options=options)
```

For `serve_command()`, the relay transports JSON-RPC frames unchanged. The
spawned ACP command and the connecting `acpremote` client must each enable the
ACP SDK's unstable protocol routes.

## Low-Level Elicitation

Use `create_elicitation()` directly for multi-field forms, URL elicitation, or
schema shapes other than one typed choice. Check `supports_elicitation(mode)`
first and construct the ACP SDK mode explicitly. `ask_choice()` is deliberately
single-select; it does not infer multi-select behavior from collection values.
