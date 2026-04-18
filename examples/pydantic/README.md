# pydantic-acp Examples

All maintained examples live under `examples/pydantic/`.

The repo now keeps two opinionated examples instead of a ladder of tiny one-off demos.

- `finance_agent.py`
  session-aware finance workspace with `ask/plan/trade` modes, structured ACP plans, approval-gated note writes, and file diff projection
- `travel_agent.py`
  travel-planning runtime with `Hooks` projection, approval-gated trip file writes, and prompt-model override behavior for image and audio prompts

## Runnable Demos

Finance agent:

```bash
uv run python -m examples.pydantic.finance_agent
```

The default model is `TestModel`, so the example runs without credentials. Set
`ACP_FINANCE_MODEL` when you want a live model.

Travel agent:

```bash
uv run python -m examples.pydantic.travel_agent
```

The travel example defaults to a deterministic local router. Set `MODEL_NAME` when you want a live
base model and `ACP_TRAVEL_MEDIA_MODEL` when you want a dedicated media fallback.

## Projection Highlights

`finance_agent.py` demonstrates:

- `FileSystemProjectionMap` read previews and write diffs
- structured native plan generation in ACP plan mode
- remembered approvals for mutating finance note writes

`travel_agent.py` demonstrates:

- `HookProjectionMap` with custom labels and hidden events
- file read/write diffs inside a generated trip workspace
- prompt-model override behavior for image and audio prompts
