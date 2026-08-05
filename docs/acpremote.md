---
title: ACP Remote
---

# ACP Remote

`acpremote` is ACP Kit's transport package for exposing an existing ACP server over WebSocket and
for mirroring a remote ACP endpoint back into a local ACP boundary.

It is transport-only. It does not adapt Pydantic AI, LangChain, or any other framework by itself.
If you already have an `acp.interfaces.Agent` or a stdio ACP command, `acpremote` can move that
ACP surface across a WebSocket boundary.

Use `acpremote` when the runtime already speaks ACP and you only need transport. Use `acpkit`
when the runtime is a Pydantic AI, LangChain, or LangGraph target and should be resolved into the
correct adapter first.

## CLI

`acpremote` installs an executable for direct transport operations.

Start here if you already have a WebSocket URL:

```bash
acpremote mirror ws://remote.example.com:8080/acp/ws
```

That command connects to the remote WebSocket and presents it locally as stdio ACP. It is the
`acpremote` equivalent of:

```bash
acpkit run --addr ws://remote.example.com:8080/acp/ws
```

Command map:

| Situation | Command | Direction |
|---|---|---|
| WebSocket endpoint already exists | `acpremote mirror ws://host:8080/acp/ws` | remote WebSocket to local stdio ACP |
| local command already speaks ACP over stdio | `acpremote expose -- <command>` | local stdio ACP to WebSocket |
| Python object is already an ACP agent | `acpremote serve module:agent` | native ACP object to WebSocket |

Expose a native ACP Python target:

```bash
acpremote serve my_app:agent --host 0.0.0.0 --port 8080
```

`serve` expects the target to already be an `acp.interfaces.Agent`. Use `acpkit serve ...` when a
framework target still needs adapter dispatch:

```bash
acpkit serve examples.pydantic.finance_agent:acp_agent --host 0.0.0.0 --port 8080
```

Expose a stdio ACP command:

```bash
acpremote expose --host 0.0.0.0 --port 8080 -- npx @zed-industries/codex-acp
```

Command options belong after `--`:

```bash
acpremote expose --cwd /srv/workspace --env MODEL=gpt-5 -- python agent.py --stdio
```

Mirror a remote endpoint back into local stdio ACP:

```bash
acpremote mirror ws://remote.example.com:8080/acp/ws
```

Auth can use either a direct token or an environment variable:

```bash
acpremote expose --token-env ACPREMOTE_TOKEN -- npx @zed-industries/codex-acp
acpremote mirror ws://remote.example.com:8080/acp/ws --bearer-token "$ACPREMOTE_TOKEN"
```

## Core Construction Paths

The public server-side seams are:

- `serve_acp(...)`
- `serve_command(...)`
- `serve_stdio_command(...)`

The public client-side seam is:

- `connect_acp(...)`

Expose an in-memory ACP agent on the remote host:

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

Mirror a remote ACP endpoint back into a local stdio ACP server:

```python
from acp import run_agent
from acpremote import connect_acp

agent = connect_acp('ws://127.0.0.1:8080/acp/ws')
await run_agent(agent)
```

If the remote server advertises `remote_cwd` in its metadata, `connect_acp(...)` uses that
directory for `new_session(...)`, `load_session(...)`, `fork_session(...)`, `resume_session(...)`,
and `list_sessions(...)` instead of forwarding the local facade's working directory verbatim.

By default `connect_acp(...)` also strips local host-backed client capabilities before forwarding
`initialize(...)` upstream. That keeps the remote ACP server authoritative for filesystem and
terminal ownership. Opt back into capability forwarding only when you explicitly want a local
client-host passthrough model:

```python
from acpremote import TransportOptions, connect_acp

agent = connect_acp(
    'ws://127.0.0.1:8080/acp/ws',
    options=TransportOptions(host_ownership='client_passthrough'),
)
```

## Typical End-To-End Flows

ACP 0.11 keeps elicitation routes behind the SDK's unstable-protocol flag. For
ACP agents that call `create_elicitation()` or `ask_choice()`, pass
`TransportOptions(use_unstable_protocol=True)` to both `serve_acp()` and
`connect_remote_agent()`. Command relays forward frames unchanged, so the
spawned stdio ACP command must also enable unstable protocol routes.

Remote-host flow:

```bash
acpkit serve examples.langchain.workspace_graph:acp_agent --host 0.0.0.0 --port 8080
```

Local mirror flow:

```bash
acpkit run --addr ws://remote.example.com:8080/acp/ws
acpremote mirror ws://remote.example.com:8080/acp/ws
```

Direct ACP transport flow:

```bash
acpremote expose --host 0.0.0.0 --port 8080 -- fast-agent --server --transport acp
```

The same flow through Python:

```python
from acpremote import serve_command

server = await serve_command(
    ["fast-agent", "--server", "--transport", "acp"],
    host="0.0.0.0",
    port=8080,
)
await server.serve_forever()
```

When an editor or launcher wants to shell out, the same mirror path can be wrapped with Toad:

```bash
toad acp "acpremote mirror ws://remote.example.com:8080/acp/ws"
```

## Default HTTP And WebSocket Surface

By default `acpremote` exposes three routes:

- metadata: `http://127.0.0.1:8080/acp`
- health: `http://127.0.0.1:8080/healthz`
- websocket: `ws://127.0.0.1:8080/acp/ws`

`mount_path=` can move the ACP metadata and WebSocket routes together while `/healthz` remains a
top-level liveness probe. `/healthz` is therefore reserved and cannot be used as the mount path.

Remote metadata discovery uses `TransportOptions.open_timeout`. A timeout leaves metadata
unavailable without blocking the WebSocket proxy indefinitely.

## Command Mirroring

`serve_command(...)` is the important seam when the upstream runtime can already speak ACP over
stdio but does not expose a reusable Python ACP agent object.

That surface is useful for tools such as:

- Codex ACP
- Fast Agent ACP
- any other ACP server that already runs over stdin and stdout

It is also the right seam for the release-prep story where the remote host owns the runtime and the
local machine only mirrors the transport.

Environment handling is additive. `env={...}` overrides selected variables while inheriting the
parent process environment, so normal `PATH` lookup still works.

For command-backed servers, `acpremote` also advertises the command's effective working directory
as `remote_cwd` in the metadata endpoint. That keeps mirrored local ACP facades aligned with the
remote host instead of the local client machine.

## Transport Timing

`TransportOptions` can emit proxy-observed timing data on the mirrored ACP stream:

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

When enabled:

- streamed updates can carry `field_meta["acpremote"]["transport_latency"]`
- a visible `Transport Latency` ACP card can be emitted after each prompt turn

These numbers are proxy-observed timings measured by the local mirror, not synchronized one-way
host clock measurements.

## Transport Contract

Current transport behavior is intentionally narrow:

- one WebSocket text message carries one ACP JSON message
- binary WebSocket frames are rejected
- transport limits are configurable through `TransportOptions`
- bearer token auth is optional
- metadata and health endpoints are served alongside the WebSocket transport

## Documented Remote-Host Flows

Remote hosting is documented as an operator pattern, not as a maintained example source package.

Guide:

- <https://vcoderun.github.io/acpkit/examples/remote-hosting/>

The documented flows cover both supported remote-host stories:

1. adapt a Python runtime through `pydantic-acp` or `langchain-acp`
2. expose the resulting ACP server through `acpkit serve ...` or `acpremote.serve_acp(...)`
3. mirror it locally with `acpkit run --addr ...`, `acpremote mirror ...`, or `connect_acp(...)`
4. or skip adaptation entirely and expose a native ACP stdio command directly through `acpremote expose ...` or `serve_command(...)`
