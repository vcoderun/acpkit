# acpremote Examples

`acpremote` examples focus on transport, not runtime adaptation.

## Expose A Stdio ACP Command Over WebSocket

```bash
uv run python .agents/skills/acpremote/examples/serve_command.py
```

By default this example exposes:

```bash
acpkit run examples.langchain.workspace_graph:acp_agent
```

over:

```text
ws://127.0.0.1:8080/acp/ws
```

Override the command with `ACPREMOTE_COMMAND` when needed.

## Mirror A Remote ACP Endpoint Locally

```bash
ACPREMOTE_URL=ws://127.0.0.1:8080/acp/ws \
uv run python .agents/skills/acpremote/examples/mirror_remote.py
```

That starts a local stdio ACP boundary backed by the remote WebSocket server.
Set `ACPREMOTE_UNSTABLE_PROTOCOL=1` when the upstream agent sends unstable ACP
0.11 client requests such as elicitation. The default remains disabled.
