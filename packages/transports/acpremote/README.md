# acpremote

`acpremote` is the generic remote transport package for ACP.

It exposes any existing `acp.interfaces.Agent` over WebSocket and can also turn a remote ACP server
back into a local ACP agent proxy. It can also mirror any stdio ACP command by spawning it as a
child process.

This package is transport-only. Use it when the runtime already speaks ACP and you want to move
that ACP surface across a WebSocket boundary. If the runtime is still a Pydantic AI or LangChain
target that needs adapter dispatch, use `acpkit` instead.

Docs:

- <https://vcoderun.github.io/acpkit/acpremote/>

Latest stable install:

```bash
uv add acpremote
```

```bash
pip install acpremote
```

## CLI

The `acpremote` executable exposes the same transport jobs as the Python API.

Use this mapping:

| You have... | Run... | Meaning |
|---|---|---|
| an exposed remote WebSocket endpoint | `acpremote mirror ws://host:8080/acp/ws` | connect to the remote endpoint and expose it locally as stdio ACP |
| a local stdio ACP command | `acpremote expose -- <command>` | spawn the command and expose it over WebSocket |
| a native Python `acp.interfaces.Agent` | `acpremote serve module:agent` | load the ACP agent and expose it over WebSocket |

If a client such as Toad asks for an ACP command for an already exposed WebSocket, give it the
mirror command:

```bash
acpremote mirror ws://remote.example.com:8080/acp/ws
```

Expose a native ACP Python target:

```bash
acpremote serve my_app:agent --host 0.0.0.0 --port 8080
```

`serve` expects `my_app:agent` to resolve to an existing `acp.interfaces.Agent`. For Pydantic AI,
LangChain, or LangGraph targets, use the root CLI so adapter dispatch stays explicit:

```bash
acpkit serve examples.langchain.workspace_graph:acp_agent --host 0.0.0.0 --port 8080
```

Expose a stdio ACP command:

```bash
acpremote expose --host 0.0.0.0 --port 8080 -- npx @zed-industries/codex-acp
```

Pass command-specific flags after `--`:

```bash
acpremote expose --cwd /srv/agent --env MODEL=gpt-5 -- python agent.py --stdio
```

Mirror a remote WebSocket endpoint back to local stdio ACP:

```bash
acpremote mirror ws://remote.example.com:8080/acp/ws
```

Register ACP 0.11 unstable client routes on that receiving upstream connection
when the remote agent uses elicitation:

```bash
acpremote mirror ws://remote.example.com:8080/acp/ws --unstable-protocol
```

That is the direct `acpremote` equivalent of:

```bash
acpkit run --addr ws://remote.example.com:8080/acp/ws
```

Bearer tokens can be passed directly or read from an environment variable:

```bash
acpremote expose --token-env ACPREMOTE_TOKEN -- npx @zed-industries/codex-acp
acpremote mirror ws://remote.example.com:8080/acp/ws --bearer-token "$ACPREMOTE_TOKEN"
```

## Server

Expose any ACP agent on the remote host:

```python
from acpremote import serve_acp

server = await serve_acp(agent=my_acp_agent, host='127.0.0.1', port=8080)
await server.serve_forever()
```

Expose a stdio ACP command instead of an in-memory agent:

```python
from acpremote import serve_command

server = await serve_command(
    ['npx', '@zed-industries/codex-acp'],
    host='127.0.0.1',
    port=8080,
)
await server.serve_forever()
```

`env={...}` overrides selected variables while inheriting the parent process environment. That
keeps command lookup through `PATH` intact while still letting the caller inject tokens or runtime
flags.

If you need command cleanup tuning, pass `CommandOptions` to `serve_stdio_command(...)`:

```python
from acpremote import CommandOptions, serve_stdio_command

server = await serve_stdio_command(
    CommandOptions(
        command=('npx', '@zed-industries/codex-acp'),
        terminate_timeout=2.0,
    ),
    host='127.0.0.1',
    port=8080,
)
await server.serve_forever()
```

When a command-backed WebSocket flow ends, `acpremote` terminates the child process and falls back
to `kill` after `terminate_timeout`. The timeout must be a positive finite number.

Typical remote-host flow:

```bash
acpkit serve examples.langchain.workspace_graph:acp_agent --host 0.0.0.0 --port 8080
acpremote expose --host 0.0.0.0 --port 8081 -- npx @zed-industries/codex-acp
```

Typical local mirror flow:

```bash
acpkit run --addr ws://remote.example.com:8080/acp/ws
acpremote mirror ws://remote.example.com:8081/acp/ws
```

Default routes:

- metadata: `http://127.0.0.1:8080/acp`
- health: `http://127.0.0.1:8080/healthz`
- websocket: `ws://127.0.0.1:8080/acp/ws`

Custom mount paths must not use `/healthz`, which is reserved for the health endpoint.

## Client Proxy

Turn a remote ACP endpoint back into a local ACP agent:

```python
from acp import run_agent
from acpremote import connect_acp

agent = connect_acp('ws://127.0.0.1:8080/acp/ws')
await run_agent(agent)
```

That pattern is what powers a local stdio ACP facade in front of a remote ACP server.

If you want a launcher to open that local facade, wrap the same mirror command with Toad:

```bash
toad acp "acpremote mirror ws://remote.example.com:8080/acp/ws"
```

When the remote server advertises a `remote_cwd` in its metadata, `connect_acp(...)` treats that
directory as authoritative for session lifecycle calls. This keeps a mirrored local ACP server from
accidentally sending the local machine's spawn directory back to the remote host.
Metadata discovery uses `TransportOptions.open_timeout`; a timeout leaves metadata unavailable
without blocking the WebSocket proxy indefinitely.

By default `connect_acp(...)` also treats host-backed capabilities as remote-owned. Local client
filesystem and terminal capabilities aren't forwarded unless `TransportOptions(host_ownership="client_passthrough")`
is set explicitly.

## Transport Timing

ACP Python SDK 0.11 elicitation routes require an explicit opt-in on the client
connection that receives `elicitation/create`. Use either public mirror CLI:

```bash
acpremote mirror ws://remote.example.com:8080/acp/ws --unstable-protocol
acpkit run --addr ws://remote.example.com:8080/acp/ws --unstable-protocol
```

For object-level mirrors, set
`TransportOptions(use_unstable_protocol=True)` on `connect_acp(...)` or
`connect_remote_agent(...)`. A sending agent can use plain `run_agent(...)`;
it does not need an agent-side unstable flag merely to issue elicitation.
Capability advertisement remains an independent requirement. `serve_command()`
is a raw frame relay and follows the same receiver-side rule.

`TransportOptions` can attach proxy-observed latency information to the ACP stream:

```python
from acpremote import TransportOptions, connect_acp

agent = connect_acp(
    'ws://127.0.0.1:8080/acp/ws',
    options=TransportOptions(
        emit_latency_meta=True,
        emit_latency_projection=True,
    ),
)
```

`TransportOptions` also controls host ownership policy:

- `host_ownership="remote"` is the default
- `host_ownership="client_passthrough"` re-enables forwarding local filesystem and terminal client capabilities

Available signals:

- streamed updates can carry `field_meta["acpremote"]["transport_latency"]`
- a visible `Transport Latency` ACP card can be emitted after each prompt turn

The metrics are proxy-observed timings, not synchronized end-to-end host clock measurements.

## Transport Notes

Current transport behavior:

- one WebSocket text message carries one ACP JSON message
- binary frames are rejected
- bearer-token auth is supported
- stdio ACP commands can be mirrored with `serve_command(...)`
- custom command cleanup timeouts are available through `CommandOptions`
- transport limits are configurable through `TransportOptions`

This package is transport-focused. It doesn't assume ACP Kit adapters or ACP Kit-owned runtime
semantics.

Security guidance:

- bind to loopback unless a reverse proxy owns TLS and authentication
- allowlist command-backed servers instead of accepting arbitrary command strings
- keep environment overrides minimal and avoid forwarding unnecessary secrets
- see <https://vcoderun.github.io/acpkit/security/>
