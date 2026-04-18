# Examples

All maintained examples live under [`examples/pydantic/`](https://github.com/vcoderun/acpkit/tree/main/examples/pydantic).

The repo intentionally keeps the maintained set small. Each example is broad enough to be useful on
its own instead of only demonstrating one narrow helper.

## Maintained Examples

| Example | What it demonstrates |
|---|---|
| [`finance_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/finance_agent.py) | session-aware finance workspace with ACP plans, approvals, mode-aware tool shaping, and projected note diffs |
| [`travel_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/travel_agent.py) | travel planning runtime with hook projection, approval-gated trip files, and prompt-model override behavior for media prompts |

## Recommended Reading Order

1. [Finance Agent](finance.md)
2. [Travel Agent](travel.md)

## Running The Examples

```bash
uv run python -m examples.pydantic.finance_agent
uv run python -m examples.pydantic.travel_agent
```

Both examples run without credentials by default. Set the example-specific environment variables
only when you want live models.
