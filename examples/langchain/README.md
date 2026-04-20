# langchain-acp Examples

All maintained LangChain examples live under `examples/langchain/`.

- `workspace_graph.py`
  module-level `graph`, session-aware `graph_from_session(...)`, and file read/write projection for
  `acpkit run ...` or remote ACP hosting
- `deepagents_graph.py`
  DeepAgents compatibility example with `DeepAgentsCompatibilityBridge` and `DeepAgentsProjectionMap`

## Runnable Demo

```bash
uv run python -m examples.langchain.workspace_graph
uv run python -m examples.langchain.deepagents_graph
```

Or expose the module-level graph directly through the root CLI:

```bash
acpkit run examples.langchain.workspace_graph:graph
acpkit run examples.langchain.deepagents_graph:graph
```

The workspace graph example also works as a remote ACP host:

```bash
acpkit serve examples.langchain.workspace_graph:graph --host 0.0.0.0 --port 8080
acpkit run --addr ws://127.0.0.1:8080/acp/ws
```
