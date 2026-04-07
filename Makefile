BLUE := \033[1;34m
GREEN := \033[1;32m
RESET := \033[0m
PYTHON_VERSIONS := 3.11.13 3.12.10 3.13.9

.PHONY: tests format check-formatted check check-matrix all prod rename serve

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
		uv run --extra dev --python $$version basedpyright --pythonversion $$short_version || exit $$?; \
	done
	@printf "$(GREEN)✔ Matrix checking complete.$(RESET)\n"

tests:
	@printf "$(BLUE)==>$(RESET) Running tests with pytest...\n"
	@uv run --extra dev pytest
	@printf "$(GREEN)✔ Tests complete.$(RESET)\n"

serve:
	@printf "$(BLUE)==>$(RESET) Serving docs with mkdocs...\n"
	@uv run --extra docs mkdocs serve --dev-addr 127.0.0.1:8080

all: format check

prod: tests format check-matrix
