# langchain-acp Examples

This skill uses the maintained public `examples/langchain/` tree as the primary
reference set.

## Best Public Examples

- [`examples/langchain/workspace_graph.py`](https://github.com/vcoderun/acpkit/blob/main/examples/langchain/workspace_graph.py)
- [`examples/langchain/deepagents_graph.py`](https://github.com/vcoderun/acpkit/blob/main/examples/langchain/deepagents_graph.py)
- [`examples/langchain/README.md`](https://github.com/vcoderun/acpkit/blob/main/examples/langchain/README.md)

## When To Use Which

Use `workspace_graph.py` for:

- module-level `graph`
- session-aware `graph_from_session(...)`
- filesystem projection
- `acpkit run ...` and `acpkit serve ...` integration

Use `deepagents_graph.py` for:

- DeepAgents compatibility
- `DeepAgentsCompatibilityBridge`
- `DeepAgentsProjectionMap`

## Cross-Package Example

For remote-host transport paired with LangChain adaptation, use:

- [serve_command.py](https://github.com/vcoderun/acpkit/blob/main/.agents/skills/acpremote/examples/serve_command.py)
- [mirror_remote.py](https://github.com/vcoderun/acpkit/blob/main/.agents/skills/acpremote/examples/mirror_remote.py)
