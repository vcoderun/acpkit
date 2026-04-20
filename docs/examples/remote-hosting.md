# Remote ACP Hosting

Source directory:

- [`examples/acpremote/`](https://github.com/vcoderun/acpkit/tree/main/examples/acpremote)

This is the maintained example set for combining ACP Kit adapters with ACP Remote transport.

## What It Demonstrates

- adapting a Pydantic AI runtime into ACP and exposing it over WebSocket
- adapting a LangChain graph into ACP and exposing it over WebSocket
- mirroring either remote endpoint back into a local stdio ACP server
- keeping remote host ownership for `cwd`, filesystem, and terminal capabilities

## Pydantic Remote Flow

Remote host:

```bash
uv run python examples/acpremote/serve_pydantic_finance.py
```

Local mirror:

```bash
ACPREMOTE_URL=ws://127.0.0.1:8080/acp/ws uv run python examples/acpremote/connect_mirror.py
```

This path uses the maintained finance example from `examples/pydantic/finance_agent.py`.

## LangChain Remote Flow

Remote host:

```bash
ACPREMOTE_PORT=8081 uv run python examples/acpremote/serve_langchain_workspace.py
```

Local mirror:

```bash
ACPREMOTE_URL=ws://127.0.0.1:8081/acp/ws uv run python examples/acpremote/connect_mirror.py
```

This path uses the maintained plain-LangChain example from `examples/langchain/workspace_graph.py`.

## CLI Alternative

If you want the same flow without a Python wrapper script, ACP Kit already exposes the same
boundary through the root CLI:

```bash
acpkit serve examples.pydantic.finance_agent:agent --host 0.0.0.0 --port 8080
acpkit serve examples.langchain.workspace_graph:graph --host 0.0.0.0 --port 8081
acpkit run --addr ws://127.0.0.1:8080/acp/ws
```

Use the scripts when you want a maintained Python entrypoint. Use the CLI when you want the
shortest operator path.
