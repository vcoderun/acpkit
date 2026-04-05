# pydantic-acp Examples

All maintained examples live under `examples/pydantic/`.

## Runnable Demos

Full end-to-end ACP demo:

```bash
uv run python -m examples.pydantic.my_agent
```

Native `Hooks` plus `HookProjectionMap` demo:

```bash
uv run python -m examples.pydantic.hook_projection
```

## Focused SDK Examples

- `static_agent.py`
  Smallest `run_acp(agent=...)` setup.
- `factory_agent.py`
  Session-aware factory plus session-local model selection.
- `providers.py`
  Models, modes, config options, plan updates, and approval-state providers.
- `bridges.py`
  `AgentBridgeBuilder`, `HookBridge`, history processors, prepare-tools modes, and MCP metadata.
- `approvals.py`
  Native deferred approval flow for approval-gated tools.
- `host_context.py`
  `ClientHostContext` usage for filesystem and terminal access inside a factory-built agent.
- `hook_projection.py`
  Real `Hooks` capability introspection rendered through `HookProjectionMap`.

## Projection Highlights

`FileSystemProjectionMap` examples in this directory cover:

- read tools rendered as diff previews
- write tools rendered as ACP file diffs
- bash tools rendered as command previews and terminal references

`HookProjectionMap` examples in this directory cover:

- existing `Hooks` capability introspection
- custom event labels
- hidden hook events
- ACP title shaping for hook updates

`my_agent.py` remains the broadest demo. It combines factories, providers, approvals, bridges,
projection maps, slash commands, and client-backed host helpers in one ACP server.
