# Testing

ACP Kit is tested primarily at the public behavior boundary, not by deeply mocking private runtime internals.

That matters for an adapter: correctness lives in session behavior, ACP updates, approvals, plan state, and tool projection more than in any one private helper.

## What The Suite Covers

The main `tests/pydantic/` suite covers:

- ACP session lifecycle
- transcript and message-history replay
- session-local model selection
- slash commands for models, modes, and thinking
- native plan state and provider-backed plan state
- deferred approval flow
- factory and `AgentSource` integration
- capability bridges
- filesystem and command projection
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

Branch coverage for the adapter:

```bash
make coverage-branch
```

Run coverage and save the formatted summary to `COVERAGE`:

```bash
make save-coverage
```

Check the coverage thresholds without rewriting tracked files:

```bash
make check-coverage
```

Focused adapter suite:

```bash
python3.11 -B -m pytest tests/pydantic tests/test_acpkit_cli.py -q
```

## Test Style

The preferred test style is:

- assert on ACP method behavior
- assert on emitted session updates
- assert on visible tool or hook listings
- assert on persisted session state
- assert on provider and bridge integration

The suite intentionally avoids:

- mocking private helper call order
- overfitting to implementation details that do not affect ACP behavior

## Docs Validation

When editing documentation, also validate the docs build:

```bash
uv run --extra docs --extra pydantic --extra codex mkdocs build --strict
```

## Pre-commit

ACP Kit keeps lightweight config hooks on every commit, and only runs expensive validation when the staged change set looks major.

- always on `pre-commit`: YAML and TOML validation
- conditional on `pre-commit`: `make check-coverage` and `make prod`
- the heavy hooks run only when staged files touch core code, tests, scripts, workflows, or tool config

Install the hook:

```bash
uv run pre-commit install
```

Force the heavy hooks even for a small staged change:

```bash
ACPKIT_FORCE_MAJOR_HOOKS=1 git commit
```
