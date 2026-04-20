# LangChain Workspace Graph

Source:

- [`examples/langchain/workspace_graph.py`](https://github.com/vcoderun/acpkit/blob/main/examples/langchain/workspace_graph.py)

This example is the maintained plain-LangChain showcase.

It demonstrates:

- a module-level `graph`, `config`, and `main()`
- a session-aware `graph_from_session(...)` factory
- `acpkit run examples.langchain.workspace_graph:graph`
- filesystem read and write projection through `FileSystemProjectionMap`
- a small seeded workspace for deterministic ACP rendering
- a clean remote-host path through `acpkit serve ...` and `acpkit run --addr ...`

Run it:

```bash
uv run python -m examples.langchain.workspace_graph
```

Or expose the graph directly through the root CLI:

```bash
acpkit run examples.langchain.workspace_graph:graph
```

Or host it remotely through ACP Remote:

```bash
acpkit serve examples.langchain.workspace_graph:graph --host 0.0.0.0 --port 8081
acpkit run --addr ws://127.0.0.1:8081/acp/ws
```
