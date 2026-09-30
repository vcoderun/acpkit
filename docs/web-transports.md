# ACP 0.12 Web Transports

ACP Kit adapters target `agent-client-protocol==0.12.1`. The SDK adds experimental
Streamable HTTP and WebSocket transports without changing the adapter contract:
`create_acp_agent(...)` still returns an ACP `Agent`, and the SDK owns the network
connection.

## Install

Install an adapter with the `web` extra:

```bash
uv add "pydantic-acp[web]"
```

```bash
pip install "pydantic-acp[web]"
```

Use `langchain-acp[web]` for a LangChain or LangGraph adapter. The root package also
provides `acpkit[web]`.

## Serve An Adapter

Create one adapter instance per ACP connection. This prevents one connection from
replacing another connection's callback client or session-local state.

```python
from acp.agent.connection import AgentSideConnection
from acp.http.asgi import create_asgi_app
from pydantic_acp import AcpAgent, AdapterConfig, create_acp_agent
from pydantic_ai import Agent


def build_agent(_connection: AgentSideConnection) -> AcpAgent:
    return create_acp_agent(
        agent=Agent("openai:gpt-5", instructions="Help with the current workspace."),
        config=AdapterConfig(),
    )


app = create_asgi_app(build_agent)
```

The resulting Starlette application serves Streamable HTTP and WebSocket ACP at
`/acp`. Use an HTTP/2-capable ASGI server such as Hypercorn for Streamable HTTP.
WebSocket operation does not require HTTP/2.

The same construction works with `langchain_acp.create_acp_agent(...)`; only the
body of `build_agent` changes.

## Connect A Client

The official client transports plug directly into `acp.connect_to_agent`:

```python
from acp import connect_to_agent
from acp.http import create_http_stream
from acp.ws import create_websocket_stream
from acp.ws.client import MemoryAcpCookieStore

# Streamable HTTP is created synchronously.
http_transport = create_http_stream("https://agents.example.com/acp")
http_connection = connect_to_agent(client, http_transport)

# WebSocket setup is asynchronous. Reuse the cookie store across reconnects
# when the deployment relies on session affinity.
cookies = MemoryAcpCookieStore()
ws_transport = await create_websocket_stream(
    "wss://agents.example.com/acp",
    cookie_store=cookies,
)
ws_connection = connect_to_agent(client, ws_transport)
```

Call `initialize(...)` before session methods and close the ACP connection when the
client is finished. The SDK transport owns framing, connection IDs, SSE streams,
cookies, and the ordered delivery fix shipped in `0.12.1`.

## Relationship To `acpremote`

The official ACP web transport and `acpremote` solve different deployment problems:

| Surface | Use it for |
|---|---|
| ACP 0.12 web transport | A native ASGI ACP endpoint and standard SDK clients |
| `acpremote` | stdio command mirroring, bearer auth, metadata discovery, transport limits, and host-ownership policy |

Do not point an official SDK client at an `acpremote` metadata endpoint. Use the ACP
endpoint advertised by that server, or keep using `acpremote.connect_acp(...)` when
the extra proxy semantics are required.

## Schema Compatibility

ACP 0.12 uses schema v1.19. ACP Kit accounts for the renamed Python fields while
preserving their wire aliases:

- plan updates use `plan_id` and serialize as `planId`
- ACP MCP descriptors use `server_id` and serialize as `serverId`
- unknown elicitation actions are rejected explicitly by typed choice helpers
- custom extension data remains in `_meta`; tolerant SDK parsing is not treated as
  permission to accept malformed adapter-owned projection payloads
