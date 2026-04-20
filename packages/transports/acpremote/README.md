# acpremote

`acpremote` is the generic remote transport package for ACP.

It exposes any existing `acp.interfaces.Agent` over WebSocket and can also turn a remote ACP server
back into a local ACP agent proxy. It can also mirror any stdio ACP command by spawning it as a
child process.

This package is transport-only. Use it when the runtime already speaks ACP and you want to move
that ACP surface across a WebSocket boundary. If the runtime is still a Python target that needs to
be resolved first, use `acpkit` instead.

Docs:

- <https://vcoderun.github.io/acpkit/acpremote/>

Install:

```bash
uv add acpremote
```

```bash
pip install acpremote
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

Typical remote-host flow:

```bash
acpkit serve examples.langchain.workspace_graph:graph --host 0.0.0.0 --port 8080
```

Typical local mirror flow:

```bash
acpkit run --addr ws://remote.example.com:8080/acp/ws
```

Default routes:

- metadata: `http://127.0.0.1:8080/acp`
- health: `http://127.0.0.1:8080/healthz`
- websocket: `ws://127.0.0.1:8080/acp/ws`

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
toad acp "acpkit run --addr ws://remote.example.com:8080/acp/ws"
```

When the remote server advertises a `remote_cwd` in its metadata, `connect_acp(...)` treats that
directory as authoritative for session lifecycle calls. This keeps a mirrored local ACP server from
accidentally sending the local machine's spawn directory back to the remote host.

By default `connect_acp(...)` also treats host-backed capabilities as remote-owned. Local client
filesystem and terminal capabilities aren't forwarded unless `TransportOptions(host_ownership="client_passthrough")`
is set explicitly.

## Transport Timing

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
- transport limits are configurable through `TransportOptions`

This package is transport-focused. It doesn't assume ACP Kit adapters or ACP Kit-owned runtime
semantics.
