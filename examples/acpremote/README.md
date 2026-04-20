# ACP Remote examples

This directory contains the maintained remote transport examples for ACP Kit.

## Adapter-backed remote hosting

These examples start from a Python runtime that ACP Kit can adapt first, then expose the resulting
ACP surface through `acpremote`.

- `serve_pydantic_finance.py`
  builds an ACP server from the maintained finance agent with `pydantic_acp.create_acp_agent(...)`
  and exposes it through `acpkit.serve_acp(...)`
- `serve_langchain_workspace.py`
  builds an ACP server from the maintained LangChain workspace graph with
  `langchain_acp.create_acp_agent(...)` and exposes it through `acpkit.serve_acp(...)`
- `connect_mirror.py`
  connects to any remote ACP WebSocket endpoint and re-exposes it locally over stdio ACP with
  transport timing enabled by default

Pydantic remote flow:

```bash
uv run python examples/acpremote/serve_pydantic_finance.py
ACPREMOTE_URL=ws://127.0.0.1:8080/acp/ws uv run python examples/acpremote/connect_mirror.py
```

LangChain remote flow:

```bash
ACPREMOTE_PORT=8081 uv run python examples/acpremote/serve_langchain_workspace.py
ACPREMOTE_URL=ws://127.0.0.1:8081/acp/ws uv run python examples/acpremote/connect_mirror.py
```

The same local mirror can also be opened through the CLI:

```bash
acpkit run --addr ws://127.0.0.1:8080/acp/ws
```

## Direct ACP command transport

These examples start from a runtime that already speaks ACP over stdio.

- `expose_codex.py`
  starts `@zed-industries/codex-acp` as a stdio ACP child process and exposes it at
  `ws://127.0.0.1:8080/acp/ws`
- `connect_codex.py`
  connects to a remote ACP WebSocket endpoint and re-exposes it locally over stdio ACP

Typical flow:

```bash
uv run python examples/acpremote/expose_codex.py
uv run python examples/acpremote/connect_codex.py
```

If you want a launcher to open the local mirror, wrap the same command with Toad:

```bash
toad acp "acpkit run --addr ws://127.0.0.1:8080/acp/ws"
```

`connect_mirror.py` and `connect_codex.py` both support transport timing metadata:

- `field_meta["acpremote"]["transport_latency"]` on streamed updates
- a visible `Transport Latency` ACP card after each prompt turn
