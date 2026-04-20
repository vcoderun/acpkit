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
acpkit serve examples.pydantic.finance_agent:agent --host 0.0.0.0 --port 8080
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
acpkit serve examples.langchain.workspace_graph:graph --host 0.0.0.0 --port 8081
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
acpkit serve examples.pydantic.finance_agent:agent --host 0.0.0.0 --port 8080
acpkit serve examples.langchain.workspace_graph:graph --host 0.0.0.0 --port 8081
acpkit run --addr ws://127.0.0.1:8080/acp/ws
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
