# Contributing

First off, thank you for considering contributing to this project! 

## Local Development Setup

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd python_template
   ```

2. **Install dependencies using `uv`:**
   ```bash
   uv venv
   source .venv/bin/activate
   uv pip install -e ".[dev]"
   ```

3. **Install pre-commit hooks:**
   ```bash
   pre-commit install
   ```

## Development Workflow

We use a `Makefile` to simplify common tasks:

- `make format`: Formats code using `ruff`.
- `make check`: Runs linters (`ruff check`) and type checkers (`basedpyright`, `ty`).
- `make tests`: Runs the test suite using `pytest`.
- `make all`: Runs formatting and checking consecutively.

Before submitting a Pull Request, please ensure `make all` and `make tests` run without any errors.

## Pull Requests

1. Create a new branch for your feature or bugfix.
2. Commit your changes (your commit will be checked by `pre-commit`).
3. Push to your branch and open a Pull Request.
4. Ensure the CI workflows (GitHub Actions) pass.