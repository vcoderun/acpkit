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
- host backends and `ClientHostContext`

## Canonical Commands

Repo-wide checks:

```bash
uv run ruff check
uv run ty check
uv run basedpyright
make check
```

Focused adapter behavior suite:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 python3.11 -B -m pytest tests/pydantic tests/test_acpkit_cli.py -q
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
