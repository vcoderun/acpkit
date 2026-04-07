# pydantic-acp Examples

All maintained examples live under `examples/pydantic/`.

These examples are organized from smallest surface to broadest runtime:

- `static_agent.py`
  smallest direct `run_acp(agent=...)` setup
- `factory_agent.py`
  session-aware factory plus session-local model selection
- `providers.py`
  host-owned models, modes, config options, and plans
- `bridges.py`
  bridge builder, prepare-tools modes, history processors, and MCP metadata
- `approvals.py`
  native deferred approval flow
- `host_context.py`
  `ClientHostContext` usage for ACP client-backed filesystem and terminal calls
- `hook_projection.py`
  existing `Hooks` capability introspection rendered through `HookProjectionMap`
- `strong_agent.py`
  full-featured workspace agent example combining factories, providers, approvals, bridges, projection maps, slash commands, host helpers, and a real Codex-backed model

## Runnable Demos

Full-featured workspace agent:

```bash
uv run python -m examples.pydantic.strong_agent
```

This example expects a local Codex login because it uses `create_codex_responses_model(...)` under
the hood.

The workspace agent exposes three modes:

- `ask`
- `plan`
- `agent`

Native `Hooks` plus `HookProjectionMap` demo:

```bash
uv run python -m examples.pydantic.hook_projection
```

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

`strong_agent.py` remains the broadest example. It combines factories, providers, approvals, bridges,
projection maps, slash commands, `ask/plan/agent` mode gating, and client-backed host helpers in one ACP server.
