BLUE := \033[1;34m
GREEN := \033[1;32m
RESET := \033[0m

.PHONY: tests format check all prod rename

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
	@printf "$(BLUE)==>$(RESET) Renaming python_template to $(RUN_ARGS)...\n"
	@python3 scripts/rename_workspace.py $(RUN_ARGS) || python scripts/rename_workspace.py $(RUN_ARGS)
	@printf "$(GREEN)✔ Project renamed to $(RUN_ARGS) successfully!$(RESET)\n"

format:
	@printf "$(BLUE)==>$(RESET) Formatting code with ruff...\n"
	@ruff format
	@printf "$(GREEN)✔ Formatting complete.$(RESET)\n"

check:
	@printf "$(BLUE)==>$(RESET) Running ruff checks and fixing issues...\n"
	@ruff check --fix --unsafe-fixes
	@printf "$(BLUE)==>$(RESET) Type checking with ty...\n"
	@ty check
	@printf "$(BLUE)==>$(RESET) Type checking with basedpyright...\n"
	@basedpyright
	@printf "$(GREEN)✔ Checking complete.$(RESET)\n"

tests:
	@printf "$(BLUE)==>$(RESET) Running tests with pytest...\n"
	@pytest
	@printf "$(GREEN)✔ Tests complete.$(RESET)\n"

all: format check

prod: tests format check