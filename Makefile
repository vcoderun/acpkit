BLUE := \033[1;34m
GREEN := \033[1;32m
RESET := \033[0m
PYTHON_VERSIONS := 3.11.13 3.12.10 3.13.9

.PHONY: tests coverage-branch check-coverage save-coverage format check-formatted check check-matrix all prod rename serve

# Hack to allow passing arguments to make commands (e.g. make rename my_project)
ifeq (rename,$(firstword $(MAKECMDGOALS)))
  # use the rest as arguments for "rename"
  RUN_ARGS := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  # ...and turn them into do-nothing targets
  $(eval $(RUN_ARGS):;@:)
endif

rename:
	@if [ -z "$(RUN_ARGS)" ]; then \
		echo "Error: Name is not provided. Usage: make rename my_awesome_project"; \
		exit 1; \
	fi
	@printf "$(BLUE)==>$(RESET) Renaming acpkit to $(RUN_ARGS)...\n"
	@python3 scripts/rename_workspace.py $(RUN_ARGS) || python scripts/rename_workspace.py $(RUN_ARGS)
	@printf "$(GREEN)✔ Project renamed to $(RUN_ARGS) successfully!$(RESET)\n"

format:
	@printf "$(BLUE)==>$(RESET) Formatting code with ruff...\n"
	@uv run ruff format
	@printf "$(GREEN)✔ Formatting complete.$(RESET)\n"

check-formatted:
	@printf "$(BLUE)==>$(RESET) Checking formatting with ruff format --check...\n"
	@uv run ruff format --check
	@printf "$(GREEN)✔ Formatting check complete.$(RESET)\n"

check:
	@printf "$(BLUE)==>$(RESET) Running ruff checks...\n"
	@uv run --extra dev ruff check
	@printf "$(BLUE)==>$(RESET) Type checking with ty...\n"
	@uv run --extra dev ty check
	@printf "$(BLUE)==>$(RESET) Type checking with basedpyright...\n"
	@uv run --extra dev basedpyright
	@printf "$(GREEN)✔ Checking complete.$(RESET)\n"

check-matrix:
	@for version in $(PYTHON_VERSIONS); do \
		short_version=$${version%.*}; \
		printf "$(BLUE)==>$(RESET) Running validation matrix for Python $$version...\n"; \
		uv run --extra dev --python $$version ruff check src/acpkit tests || exit $$?; \
		uv run --extra dev --python $$version ty check --python-version $$short_version || exit $$?; \
		uv run --extra dev basedpyright --pythonversion $$short_version src packages tests || exit $$?; \
	done
	@printf "$(GREEN)✔ Matrix checking complete.$(RESET)\n"

tests:
	@printf "$(BLUE)==>$(RESET) Running tests with pytest...\n"
	@uv run --extra dev pytest
	@printf "$(GREEN)✔ Tests complete.$(RESET)\n"

coverage-branch:
	@printf "$(BLUE)==>$(RESET) Running branch coverage for adapter packages...\n"
	@uv run --extra dev pytest -p pytest_cov tests/pydantic tests/langchain tests/test_acpkit_cli.py tests/test_native_pydantic_agent.py tests/test_native_langchain_agent.py --cov=packages/adapters/pydantic-acp/src/pydantic_acp --cov=packages/adapters/langchain-acp/src/langchain_acp --cov-branch --cov-report=json -q
	@printf "$(GREEN)✔ Branch coverage complete. See coverage.json.$(RESET)\n"

check-coverage:
	@printf "$(BLUE)==>$(RESET) Checking line and branch coverage thresholds for adapter packages...\n"
	@set -e; \
		tmp_file=$$(mktemp "$${TMPDIR:-/tmp}/acpkit-coverage.XXXXXX"); \
		trap 'rm -f "$$tmp_file"' EXIT; \
		uv run --extra dev pytest -p pytest_cov tests/pydantic tests/langchain tests/test_acpkit_cli.py tests/test_native_pydantic_agent.py tests/test_native_langchain_agent.py --cov=packages/adapters/pydantic-acp/src/pydantic_acp --cov=packages/adapters/langchain-acp/src/langchain_acp --cov-branch --cov-report=json:$$tmp_file -q; \
		uv run --extra dev python scripts/save_coverage_summary.py --input "$$tmp_file" --check-only
	@printf "$(GREEN)✔ Coverage thresholds satisfied.$(RESET)\n"

save-coverage:
	@printf "$(BLUE)==>$(RESET) Running line and branch coverage for adapter packages...\n"
	@uv run --extra dev pytest -p pytest_cov tests/pydantic tests/langchain tests/test_acpkit_cli.py tests/test_native_pydantic_agent.py tests/test_native_langchain_agent.py --cov=packages/adapters/pydantic-acp/src/pydantic_acp --cov=packages/adapters/langchain-acp/src/langchain_acp --cov-branch --cov-report=json -q
	@printf "$(BLUE)==>$(RESET) Saving coverage summary to COVERAGE...\n"
	@uv run --extra dev python scripts/save_coverage_summary.py
	@printf "$(GREEN)✔ Coverage summary written to COVERAGE.$(RESET)\n"

serve:
	@printf "$(BLUE)==>$(RESET) Serving docs with mkdocs...\n"
	@uv run --extra docs --extra pydantic --extra codex mkdocs serve --dev-addr 127.0.0.1:8080

all: format check

prod: tests format check-matrix

pre-commit:
	@printf "$(BLUE)==>$(RESET) Running pre-commit checks...\n"
	@uv run --extra dev pre-commit
	@printf "$(GREEN)✔ Pre-commit checks complete.$(RESET)\n"
