# pydantic-acp Examples

All maintained examples live under `examples/pydantic/`.

These examples are arranged from the smallest adapter surface to richer ACP-aware runtimes:

- `static_agent.py`
  smallest direct `run_acp(agent=...)` setup
- `factory_agent.py`
  session-aware factory plus session-local model selection
- `providers.py`
  host-owned models, modes, config options, plan state, and approval metadata
- `bridges.py`
  bridge builder, prepare-tools modes, thread executor wiring, tool metadata, return schemas, and builtin tool projection
- `approvals.py`
  native deferred approval flow
- `host_context.py`
  `ClientHostContext` usage for ACP client-backed filesystem and terminal calls
- `hook_projection.py`
  existing `Hooks` capability introspection rendered through `HookProjectionMap`
- `strong_agent.py`
  compact workspace agent with `ask/plan/agent` modes, structured native plans, approvals, projection maps, and host-backed tools
- `strong_agent_v2.py`
  prompt-model override example for image and audio prompts, plus repository-read projection

## Runnable Demos

Workspace agent:

```bash
uv run python -m examples.pydantic.strong_agent
```

The default model is `TestModel`, so the example runs without external credentials. Set
`ACP_WORKSPACE_MODEL` when you want a live model.

The workspace example exposes three ACP-visible modes:

- `ask`
- `plan`
- `agent`

Media-aware prompt override example:

```bash
uv run python -m examples.pydantic.strong_agent_v2
```

This example also defaults to `TestModel`. Set `MODEL_NAME` when you want a live base model and
`ACP_MEDIA_MODEL` when you want a dedicated media fallback.

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

`strong_agent.py` is the main workspace showcase. It stays small enough to read in one pass while
still demonstrating ACP-native plans, approvals, projection maps, and host-backed tools.
