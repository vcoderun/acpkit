# Python Template

A modern, fully-featured boilerplate for scaling Python projects from development to production.

## ✨ Features

- **Package Manager:** Lightning-fast environment & dependency management using [uv](https://github.com/astral-sh/uv).
- **Linting & Formatting:** Extremely fast code analysis and formatting via [Ruff](https://github.com/astral-sh/ruff).
- **Type Checking:** Strict and modern type testing with [basedpyright](https://github.com/DetachHead/basedpyright) and [ty](https://github.com/tyneai/ty).
- **Testing:** Out-of-the-box setup for [pytest](https://docs.pytest.org/en/latest/).
- **Documentation:** Beautiful docs setup using [MkDocs Material](https://squidfunk.github.io/mkdocs-material/).
- **CI/CD Pipeline:** GitHub Actions for automated testing (multi-Python matrix), PyPI trusted publishing, and GitHub Pages deployments.
- **Developer Experience:** Integrated VS Code settings, `pre-commit` hooks, and a straightforward `Makefile` for daily tasks.
- **Community Ready:** Issue/PR templates, `CONTRIBUTING.md`, `SECURITY.md`, and an `MIT` License.

## 🚀 Getting Started

### Prerequisites
Make sure you have [uv](https://github.com/astral-sh/uv) installed on your system.

### Installation

1. Clone this repository (or use it as a GitHub Template):
   ```bash
   git clone https://github.com/yourusername/python_template.git
   cd python_template
   ```

2. Rename the template to your own project name (updates files, folders, and configs):
   ```bash
   make rename my_new_project
   ```

3. Create a virtual environment and install dependencies:
   ```bash
   uv venv
   source .venv/bin/activate
   uv pip install -e ".[dev]"
   ```

4. Install pre-commit hooks to ensure code quality before commits:
   ```bash
   pre-commit install
   ```

## 🛠️ Development Workflow

A simple `Makefile` is provided to run common development tasks:

- `make format`: Auto-formats the codebase using Ruff.
- `make check`: Runs Ruff linter and strict type-checkers (basedpyright & ty).
- `make tests`: Executes the pytest suite.
- `make all`: Runs `format` followed by `check`.

*Before pushing your code or opening a PR, always ensure `make all` and `make tests` pass smoothly.*

## 📚 Documentation

To preview the project documentation locally:

```bash
mkdocs serve --dev-addr 127.0.0.1:8080
```
This will start a local live-reloading server at `http://127.0.0.1:8080`.

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.