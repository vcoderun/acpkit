BLUE := \033[1;34m
GREEN := \033[1;32m
RESET := \033[0m
PYTHON_VERSIONS := 3.11.13 3.12.10 3.13.9

.PHONY: tests format check check-matrix all prod rename

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

check:
	@printf "$(BLUE)==>$(RESET) Running ruff checks...\n"
	@uv run ruff check
	@printf "$(BLUE)==>$(RESET) Type checking with ty...\n"
	@uv run ty check
	@printf "$(BLUE)==>$(RESET) Type checking with basedpyright...\n"
	@uv run basedpyright
	@printf "$(GREEN)✔ Checking complete.$(RESET)\n"

check-matrix:
	@for version in $(PYTHON_VERSIONS); do \
		short_version=$${version%.*}; \
		printf "$(BLUE)==>$(RESET) Running validation matrix for Python $$version...\n"; \
		uv run --python $$version ruff check src/acpkit tests || exit $$?; \
		uv run --python $$version ty check --python-version $$short_version || exit $$?; \
		uv run --python $$version basedpyright --pythonversion $$short_version || exit $$?; \
	done
	@printf "$(GREEN)✔ Matrix checking complete.$(RESET)\n"

tests:
	@printf "$(BLUE)==>$(RESET) Running tests with pytest...\n"
	@uv run pytest
	@printf "$(GREEN)✔ Tests complete.$(RESET)\n"

all: format check

prod: tests format check-matrix
