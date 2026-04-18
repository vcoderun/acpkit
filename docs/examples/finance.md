# Finance Agent

The maintained finance showcase is [`examples/pydantic/finance_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/finance_agent.py).

It is the main example for:

- a direct module-level `Agent(...)` plus `AdapterConfig(...)` surface
- `PrepareToolsBridge` mode shaping
- structured native plan generation
- approval-gated writes with `FileSystemProjectionMap`
- file-backed ACP session persistence

## Why This Example Exists

Older examples in this repo split these ideas across separate files. That made the codebase noisy
and kept coverage low in files that were too small to justify keeping around.

`finance_agent.py` intentionally keeps those ACP surfaces together in one realistic workflow:

- `ask` mode for read-only inspection
- `plan` mode for ACP-native structured plans
- `trade` mode for approval-gated note updates

## Run It

```bash
uv run python -m examples.pydantic.finance_agent
```

By default the example uses `TestModel`. Set `ACP_FINANCE_MODEL` when you want a live model.

## Key Patterns

- the module exports plain `agent`, `config`, and `main` symbols without factory wrappers
- `FinancePlanPersistenceProvider` writes ACP plans into `.acpkit/plans/`
- `PrepareToolsBridge` keeps `ask`, `plan`, and `trade` behaviors explicit instead of scattering them across separate examples
- `FileSystemProjectionMap` turns note reads and writes into rich ACP diffs
- `NativeApprovalBridge` keeps mutating writes truthfully approval-gated
