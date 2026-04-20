# Testing

ACP Kit is tested primarily at the public behavior boundary, not by deeply mocking private runtime internals.

That matters for an adapter: correctness lives in session behavior, ACP updates, approvals, plan state, and tool projection more than in any one private helper.

## What The Suite Covers

The adapter suites cover both runtime families:

- ACP session lifecycle
- transcript and message-history replay
- session-local model selection
- session-local mode and config-option state
- native plan state and provider-backed plan state
- deferred approval flow
- factory and source-object integration
- capability bridges
- filesystem and command projection
- structured event projection
- DeepAgents compatibility behavior
- host backends and `ClientHostContext`
- Codex auth helper integration

Recent high-value scenarios include:

- persisted file-backed session restart and continuation
- malformed saved session files in public load/list flows
- interleaved multi-session isolation
- root CLI -> adapter entrypoint routing
- hook event-stream contract failures

## Canonical Commands

Repo-wide checks:

```bash
uv run ruff check
uv run ty check
uv run basedpyright
make tests
make check
```

Branch coverage for the adapter packages:

```bash
make coverage-branch
```

Run coverage and save the formatted summary to `COVERAGE`:

```bash
make save-coverage
```

Current enforced thresholds:

- line coverage must stay at or above `97%`
- branch coverage must stay at or above `95%`

Check the coverage thresholds without rewriting tracked files:

```bash
make check-coverage
```

Focused adapter suites:

```bash
python3.11 -B -m pytest tests/pydantic tests/test_acpkit_cli.py -q
```

```bash
python3.11 -B -m pytest tests/langchain tests/test_native_langchain_agent.py -q
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
uv run --extra docs --extra pydantic --extra langchain --extra codex mkdocs build --strict
```

## Pre-commit

ACP Kit keeps lightweight config hooks on every commit, and only runs expensive validation when the staged change set looks major.

- always on `pre-commit`: `uv run --extra dev ruff check --fix`, YAML validation, and TOML validation
- conditional on `pre-commit`: `make check-coverage` and `make prod`
- the heavy hooks run only when staged files touch core code, tests, scripts, workflows, or tool config

That split is intentional:

- normal commits stay fast
- major runtime, test, tooling, or workflow changes still hit the stronger gate

Install the hook:

```bash
uv run pre-commit install
```

Force the heavy hooks even for a small staged change:

```bash
ACPKIT_FORCE_MAJOR_HOOKS=1 git commit
```
