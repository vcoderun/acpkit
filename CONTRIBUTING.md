# Contributing

ACP Kit uses `uv` for dependency management, tool execution, and workspace syncing.
Use the repo commands below instead of ad hoc `pip`, `poetry`, or one-off local flows.

## Prerequisites

- Python `3.11+`
- `uv`
- `git`

## Clone And Install

```bash
git clone https://github.com/vcoderun/acpkit
cd acpkit
uv sync --all-extras
```

`uv sync --all-extras` is the simplest contributor setup. It installs the root package,
workspace packages, test tools, docs tools, and the `pydantic-acp` adapter surface used by
most development work.

If you want a narrower environment, the minimum useful setup for code contributions is:

```bash
uv sync --extra dev --extra pydantic
```

For docs work without the full extras set:

```bash
uv sync --extra dev --extra docs --extra pydantic --extra codex
```

You usually do not need to activate `.venv` manually because the commands below use `uv run`.

## Pre-commit

Install hooks with:

```bash
uv run pre-commit install
```

Run them manually with:

```bash
uv run pre-commit run --all-files
```

Current hook behavior:

- validates YAML and TOML files
- runs `uv run --extra dev ruff check --fix`
- checks that local package versions are ahead of the latest published PyPI versions
- runs `make check-coverage` and `make prod` only for major staged changes

If `ruff check --fix` rewrites files, re-stage them before committing.

## Common Commands

Formatting and validation:

- `make format`: run `ruff format`
- `make check-formatted`: verify formatting with `ruff format --check`
- `make check`: run `ruff check`, `ty check`, and `basedpyright`
- `make all`: run `make format` and `make check`

Tests and coverage:

- `make tests`: run the full test suite with `pytest`
- `make check-coverage`: enforce line and branch coverage thresholds for `pydantic-acp`
- `make save-coverage`: run the same coverage job and write the summary to [COVERAGE](https://github.com/vcoderun/acpkit/blob/main/COVERAGE)
- `make check-matrix`: run lint and type checks across the supported Python version matrix

Docs:

- `make serve`: run the MkDocs dev server on `127.0.0.1:8080`
- `uv run --extra docs --extra pydantic --extra codex mkdocs build --strict`: run the strict docs build used in CI

Higher-cost validation:

- `make prod`: run `tests`, `format`, and the multi-version `check-matrix` gate

## Recommended Local Workflow

For most changes:

1. Sync dependencies with `uv sync --all-extras`.
2. Make your change.
3. Run `make format`.
4. Run `make check`.
5. Run `make tests`.
6. Run `uv run pre-commit run --all-files` if you want the same hook surface before commit.

Run the stricter commands when they matter:

- If you touched `pydantic-acp` runtime, plan, approval, bridge, or projection code, run `make check-coverage`.
- If you touched docs or README-linked docs surfaces, run `uv run --extra docs --extra pydantic --extra codex mkdocs build --strict`.
- If you changed packaging, CI, or anything release-sensitive, run `make prod`.

## Pull Requests

Before opening a pull request:

1. Make sure your branch is up to date with `main`.
2. Ensure `make check` and `make tests` pass.
3. Run the stricter coverage or docs gates when your changes affect those surfaces.
4. Commit with a clean `pre-commit` result.
5. Push your branch and verify GitHub Actions passes.

## Notes

- Prefer `uv run ...` over activating the environment and calling tools directly.
- Do not add new contributor instructions that depend on `pip`, `poetry`, `pdm`, or `pip-tools`.
- If you change the hook configuration, reinstall hooks with `uv run pre-commit install`.
