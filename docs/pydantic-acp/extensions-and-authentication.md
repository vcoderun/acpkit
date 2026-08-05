# Extensions And Authentication

`pydantic-acp` keeps protocol-specific escape hatches separate from capability
projection. Use `AdapterConfig.extension_router` for legacy application-owned
ACP extension messages, `AdapterConfig.contextual_extension_router` when that
handler also needs public connection state, and
`AdapterConfig.authentication_provider` for advertised authentication methods
and authentication execution.

Both seams are optional. Without them, extension methods still return ACP
`method_not_found`, extension notifications are ignored, no authentication
methods are advertised, and `authenticate()` remains a no-op.

## Extension Routing

Implement `ExtensionRouter` when an ACP client and your application share a
private or experimental JSON-RPC method that is not represented by a focused
ACP Kit bridge:

```python
from dataclasses import dataclass, field

from acp.exceptions import RequestError
from pydantic_acp import ExtensionRouter, JsonValue


@dataclass(slots=True)
class RuntimeExtensionRouter:
    changed_sessions: set[str] = field(default_factory=set)

    async def handle_method(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if method != "acme/runtime-state":
            raise RequestError.method_not_found(method)

        session_id = params.get("sessionId")
        if not isinstance(session_id, str):
            raise RequestError.invalid_params({"field": "sessionId"})
        return {
            "sessionId": session_id,
            "changed": session_id in self.changed_sessions,
        }

    async def handle_notification(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> None:
        if method != "acme/runtime-changed":
            return
        session_id = params.get("sessionId")
        if isinstance(session_id, str):
            self.changed_sessions.add(session_id)
```

Pass the router through `AdapterConfig`:

```python
from pydantic_ai import Agent
from pydantic_acp import AdapterConfig, create_acp_agent

router = RuntimeExtensionRouter()
agent = Agent("openai:gpt-5", name="extension-agent")

acp_agent = create_acp_agent(
    agent=agent,
    config=AdapterConfig(extension_router=router),
)
```

The adapter forwards JSON-compatible parameters and returns the router's JSON
object unchanged. It does not catch `RequestError`, so `method_not_found`,
`invalid_params`, `auth_required`, and other structured ACP errors retain their
original error code and data across stdio and `acpremote` transports.

Treat extension parameters as untrusted input. Validate required fields in the
router, namespace private method names, and include an application-level schema
version when the payload will evolve independently of ACP.

## Connection-Aware Extension Routing

Use `ContextualExtensionRouter` when a custom method or notification must
inspect negotiated ACP state or call the connected client. Its immutable
`ExtensionContext` contains only public, connection-scoped values:

- `client: acp.interfaces.Client`
- `protocol_version: int`
- `client_capabilities: acp.schema.ClientCapabilities | None`
- `client_info: acp.schema.Implementation | None`

```python
from acp.exceptions import RequestError
from pydantic_acp import ExtensionContext, JsonValue


class ClientAwareExtensions:
    async def handle_method(
        self,
        context: ExtensionContext,
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if method != "acme/connection-info":
            raise RequestError.method_not_found(method)

        return {
            "clientName": (
                context.client_info.name
                if context.client_info is not None
                else None
            ),
            "protocolVersion": context.protocol_version,
        }

    async def handle_notification(
        self,
        context: ExtensionContext,
        method: str,
        params: dict[str, JsonValue],
    ) -> None:
        await context.client.ext_notification(
            "acme/extension-observed",
            {"method": method, "params": params},
        )
```

Configure exactly one router contract:

```python
from pydantic_acp import AdapterConfig

config = AdapterConfig(
    contextual_extension_router=ClientAwareExtensions(),
)
```

`extension_router` and `contextual_extension_router` are mutually exclusive so
ACP Kit never guesses a handler signature by catching `TypeError`. Existing
`ExtensionRouter` implementations remain unchanged. Each ACP connection gets
its own context and negotiated state; concurrent clients do not overwrite one
another. The context deliberately excludes adapter session stores, Pydantic AI
graph state, and private runtime helpers.

## Authentication Providers

`AuthenticationProvider` owns two related operations:

- `get_auth_methods(client_capabilities)` contributes typed ACP auth methods to
  `InitializeResponse.auth_methods`.
- `authenticate(method_id)` executes the selected method and returns an
  optional `AuthenticateResponse`.

Methods can be agent-managed, environment-variable based, or terminal based.
The provider methods may be synchronous or asynchronous.

```python
import os

from acp.exceptions import RequestError
from acp.schema import (
    AuthenticateResponse,
    AuthEnvVar,
    ClientCapabilities,
    EnvVarAuthMethod,
)
from pydantic_acp import AuthenticationMethod


class EnvironmentAuthenticationProvider:
    def get_auth_methods(
        self,
        client_capabilities: ClientCapabilities | None,
    ) -> tuple[AuthenticationMethod, ...]:
        del client_capabilities
        return (
            EnvVarAuthMethod(
                id="acme-token",
                name="Acme API token",
                description="Authenticate the ACP agent with an Acme token.",
                vars=[AuthEnvVar(name="ACME_API_TOKEN", label="API token")],
                type="env_var",
            ),
        )

    async def authenticate(self, method_id: str) -> AuthenticateResponse:
        if method_id != "acme-token":
            raise RequestError.invalid_params({"methodId": method_id})
        if not os.environ.get("ACME_API_TOKEN"):
            raise RequestError.auth_required()
        return AuthenticateResponse()
```

```python
from pydantic_acp import AdapterConfig

config = AdapterConfig(
    authentication_provider=EnvironmentAuthenticationProvider(),
)
```

ACP 0.11 allows `TerminalAuthMethod` advertisement only when the client reports
`client_capabilities.auth.terminal=True`. The adapter enforces that rule by
filtering terminal methods for clients that do not advertise support. The full
`ClientCapabilities` value is still passed to the provider so it can make
additional application-specific choices.

ACP Kit materializes and validates the provider's methods once per
`initialize()` call. IDs must be nonblank and unique. After capability
filtering, the advertised IDs are stored as an immutable connection-local set;
`authenticate()` rejects unknown or filtered IDs before invoking the provider.
This keeps authentication execution aligned with exactly what that client was
shown and does not re-run `get_auth_methods()` during authentication.

Do not put credentials in auth method metadata, extension results, logs, or
session metadata. Auth method descriptors advertise how authentication works;
secret acquisition and storage remain client or application responsibilities.

## Choosing The Correct Seam

| Requirement | Use | Why |
|---|---|---|
| Project Pydantic AI capabilities, tools, modes, plans, or metadata into ACP | `CapabilityBridge` or another focused bridge | Bridges translate known runtime semantics and participate in adapter-managed session behavior. |
| Handle a private or experimental JSON-RPC method or notification without connection state | `ExtensionRouter` | The legacy router is a narrow protocol escape hatch and preserves ACP error semantics. |
| Handle custom JSON-RPC traffic that needs the connected client or negotiated state | `ContextualExtensionRouter` | `ExtensionContext` exposes typed public ACP connection facts without private adapter state. |
| Advertise and execute application authentication | `AuthenticationProvider` | Authentication is lifecycle state, not tool or capability projection. |
| Implement an agent whose protocol behavior is mostly custom ACP | Native `acp.interfaces.Agent` | Native passthrough avoids forcing application-specific lifecycle rules through the Pydantic adapter. |

Do not use an extension router to replace an existing bridge, provider, session
store, approval flow, or host backend. When an experimental ACP message gains a
dedicated stable adapter mapping, migrate to that focused surface so capability
advertisement and session ownership remain truthful.

## Session Interaction

An extension payload may carry a public ACP session id, but neither router
receives `PydanticAcpAgent` or another private runtime object. Inject
application-owned collaborators directly into the router. Use
`ContextualExtensionRouter` only when the custom protocol operation requires
the public connected client, protocol version, capabilities, or peer metadata.
For prompt-time user interaction, use the public `AcpSessionContext.client`,
capability predicates, `create_elicitation()`, and `ask_choice()` APIs from
agent factories, providers, slash commands, or bridges.

This separation keeps custom protocol routing independent from adapter session
internals and makes the same router behavior testable over local stdio and
`acpremote` forwarding.

There is also no catch-all lifecycle middleware. Authentication belongs to
`AuthenticationProvider`; models, modes, config, plans, sessions, and prompt
behavior remain in their focused provider and bridge seams. Add a lifecycle
hook only for a concrete operation that those contracts cannot represent.
