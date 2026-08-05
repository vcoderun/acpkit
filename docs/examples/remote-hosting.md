# Remote ACP Hosting

This page documents the remote-host pattern for combining ACP Kit adapters with ACP Remote
transport.

There is intentionally no maintained `examples/acpremote/` source directory. The remote-host flow is
described here as an operator pattern and mock wiring sketch so the public docs can stay stable
without shipping another example package surface.

## What It Demonstrates

- adapting a Pydantic AI runtime into ACP and exposing it over WebSocket
- adapting a LangChain graph into ACP and exposing it over WebSocket
- mirroring either remote endpoint back into a local stdio ACP server
- keeping remote host ownership for `cwd`, filesystem, and terminal capabilities

## Pydantic Remote Flow

Remote host:

```bash
acpkit serve examples.pydantic.finance_agent:acp_agent --host 0.0.0.0 --port 8080
```

Local mirror:

```bash
acpkit run --addr ws://127.0.0.1:8080/acp/ws
```

This path uses the maintained finance example from
[`examples/pydantic/finance_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/finance_agent.py).

## LangChain Remote Flow

Remote host:

```bash
acpkit serve examples.langchain.workspace_graph:acp_agent --host 0.0.0.0 --port 8081
```

Local mirror:

```bash
acpkit run --addr ws://127.0.0.1:8081/acp/ws
```

This path uses the maintained plain-LangChain example from
[`examples/langchain/workspace_graph.py`](https://github.com/vcoderun/acpkit/blob/main/examples/langchain/workspace_graph.py).

## CLI Alternative

ACP Kit already exposes the remote-host boundary through the root CLI:

```bash
acpkit serve examples.pydantic.finance_agent:acp_agent --host 0.0.0.0 --port 8080
acpkit serve examples.langchain.workspace_graph:acp_agent --host 0.0.0.0 --port 8081
acpkit run --addr ws://127.0.0.1:8080/acp/ws
```

If the WebSocket is already exposed and you only need to connect a local ACP client to it, use:

```bash
acpremote mirror ws://127.0.0.1:8080/acp/ws
```

When the upstream agent sends ACP 0.11 unstable client requests such as
elicitation, opt the receiving mirror connection in explicitly:

```bash
acpkit run --addr ws://127.0.0.1:8080/acp/ws --unstable-protocol
acpremote mirror ws://127.0.0.1:8080/acp/ws --unstable-protocol
```

This flag belongs to the mirror's upstream client connection. The sending
agent does not need it merely to issue elicitation, and the downstream client
must still advertise form support independently.

Use that command directly in launchers that expect a stdio ACP command.

Use the standalone `acpremote` CLI when the runtime already speaks ACP and no adapter dispatch is
needed.

Native ACP command on the remote host:

```bash
acpremote expose --host 0.0.0.0 --port 8082 -- npx @zed-industries/codex-acp
```

Native ACP Python target on the remote host:

```bash
acpremote serve my_native_acp_app:agent --host 0.0.0.0 --port 8083
```

Local mirror:

```bash
acpremote mirror ws://127.0.0.1:8082/acp/ws
```

## Mock Python Sketch

If you want the same shape in Python instead of the CLI, the transport boundary looks like this:

```python
from acpkit import create_acp_agent
from acpremote import connect_acp, serve_acp


async def remote_host() -> None:
    acp_agent = create_acp_agent(...)
    server = await serve_acp(agent=acp_agent, host='0.0.0.0', port=8080)
    await server.serve_forever()


async def local_mirror() -> None:
    agent = connect_acp('ws://127.0.0.1:8080/acp/ws')
    ...
```

Treat that as a documented sketch rather than a maintained example module.
