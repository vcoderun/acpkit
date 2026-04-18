# Travel Agent

The maintained travel showcase is [`examples/pydantic/travel_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/travel_agent.py).

It is the main example for:

- `Hooks` capability introspection rendered through `HookProjectionMap`
- approval-gated read/write diff projection in a local workspace
- prompt-model override behavior for image and audio prompts
- a direct module-level `Agent(...)` plus `AdapterConfig(...)` surface without example-only factories

## Run It

```bash
uv run python -m examples.pydantic.travel_agent
```

Without `MODEL_NAME`, the example uses `TestModel` so the demo remains credential-free. Set
`MODEL_NAME` and optionally `ACP_TRAVEL_MEDIA_MODEL` when you want live-model behavior.

## Key Patterns

- the module exports plain `agent`, `config`, and `main` symbols without factory wrappers
- `HookProjectionMap` relabels and hides selected hook lifecycle events
- `TravelPromptModelProvider` shows how a host can supply an explicit media-model override
- generated trip files keep the example self-contained instead of relying on tracked demo fixtures
- `FileSystemProjectionMap` turns travel file reads and writes into ACP-visible diffs
