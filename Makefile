BLUE := \033[1;34m
GREEN := \033[1;32m
RESET := \033[0m
PYTHON_VERSIONS := 3.11.13 3.12.10 3.13.9
PYDANTIC_AI_VERSIONS := 2.9.0 2.9.1 2.10.0 2.11.0 2.12.0 2.13.0 2.14.0 2.14.1 2.15.0 2.16.0 2.17.0 2.18.0 2.19.0 2.20.0 2.21.0 2.22.0 2.23.0
LANGCHAIN_VERSION := 1.3.11
LANGGRAPH_VERSION := 1.2.7
DEEPAGENTS_VERSION := 0.6.12
RELEASE_TAG ?=
DIST_DIR ?= dist

.PHONY: tests coverage-branch check-coverage save-coverage format check-formatted check check-matrix check-pydantic-ai-matrix check-langchain-stack docs-build release-check release-artifacts release all prod rename serve

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
		uv run --extra dev --python $$version basedpyright --pythonversion $$short_version src packages tests || exit $$?; \
	done
	@printf "$(GREEN)✔ Matrix checking complete.$(RESET)\n"

check-pydantic-ai-matrix:
	@for version in $(PYDANTIC_AI_VERSIONS); do \
		printf "$(BLUE)==>$(RESET) Checking Pydantic AI $$version compatibility...\n"; \
		PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --package pydantic-acp --with pytest --with pytest-asyncio --with pyyaml --with openai --with mcp --with fastmcp --with "pydantic-ai-slim==$$version" pytest -p pytest_asyncio.plugin tests/pydantic tests/test_native_pydantic_agent.py -q || exit $$?; \
		uv run --package pydantic-acp --with pytest --with pytest-asyncio --with pyyaml --with ty --with openai --with mcp --with fastmcp --with "pydantic-ai-slim==$$version" ty check packages/adapters/pydantic-acp/src tests/pydantic examples/pydantic tests/test_native_pydantic_agent.py || exit $$?; \
	done
	@printf "$(GREEN)✔ Pydantic AI compatibility matrix complete.$(RESET)\n"

check-langchain-stack:
	@printf "$(BLUE)==>$(RESET) Checking LangChain $(LANGCHAIN_VERSION), LangGraph $(LANGGRAPH_VERSION), and DeepAgents $(DEEPAGENTS_VERSION)...\n"
	@uv run --extra dev --with "langchain==$(LANGCHAIN_VERSION)" --with "langgraph==$(LANGGRAPH_VERSION)" --with "deepagents==$(DEEPAGENTS_VERSION)" pytest tests/langchain tests/test_native_langchain_agent.py -q
	@uv run --extra dev --with "langchain==$(LANGCHAIN_VERSION)" --with "langgraph==$(LANGGRAPH_VERSION)" --with "deepagents==$(DEEPAGENTS_VERSION)" ty check packages/adapters/langchain-acp/src tests/langchain examples/langchain tests/test_native_langchain_agent.py
	@printf "$(GREEN)✔ LangChain stack compatibility checks complete.$(RESET)\n"

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

docs-build:
	@printf "$(BLUE)==>$(RESET) Building documentation with strict validation...\n"
	@uv run --extra docs --extra pydantic --extra codex mkdocs build --strict
	@printf "$(GREEN)✔ Documentation build complete.$(RESET)\n"

release-check:
	@printf "$(BLUE)==>$(RESET) Validating synchronized release metadata...\n"
	@uv run --extra dev python scripts/release.py check $(if $(RELEASE_TAG),--tag "$(RELEASE_TAG)")
	@printf "$(GREEN)✔ Release metadata valid.$(RESET)\n"

release-artifacts:
	@if [ -z "$(RELEASE_TAG)" ]; then \
		echo "Error: RELEASE_TAG is required. Usage: make release RELEASE_TAG=v1.0.0_2026-07-04"; \
		exit 1; \
	fi
	@printf "$(BLUE)==>$(RESET) Building and smoke testing release artifacts...\n"
	@uv run --extra dev python scripts/release.py prepare --tag "$(RELEASE_TAG)" --output-dir "$(DIST_DIR)"
	@printf "$(GREEN)✔ Release artifacts are ready in $(DIST_DIR).$(RESET)\n"

all: format check

prod: tests check-formatted check check-coverage docs-build check-matrix check-pydantic-ai-matrix check-langchain-stack release-check

release: prod release-artifacts

pre-commit:
	@printf "$(BLUE)==>$(RESET) Running pre-commit checks...\n"
	@uv run --extra dev pre-commit
	@printf "$(GREEN)✔ Pre-commit checks complete.$(RESET)\n"
