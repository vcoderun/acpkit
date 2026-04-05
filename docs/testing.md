# Testing

The project’s main behavioral contract lives in the `tests/pydantic/` package.

## What The Tests Cover

- ACP session lifecycle
- transcript and history replay
- session-local model selection
- deferred approval flow
- factory and `AgentSource` integration
- provider-backed modes, config options, plans, and approval metadata
- capability bridges
- slash commands and model mutation fallback
- filesystem and bash projection maps
- host backends and `ClientHostContext`
- Codex auth helper integration

## Canonical Commands

Repo-wide checks:

```bash
uv run ruff check
uv run ty check
uv run basedpyright
make tests
make check
```

Focused adapter suite:

```bash
python -m pytest tests/pydantic tests/test_acpkit_cli.py -q
```

## Test Style

The adapter is primarily tested at the public boundary:

- ACP method behavior
- session updates
- tool projection
- approval paths
- bridge emissions
- provider integration

The suite intentionally avoids deep mocking of private runtime internals.
