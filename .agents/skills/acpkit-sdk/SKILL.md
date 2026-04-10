---
name: acpkit-sdk
description: Use for ACP Kit SDK work that turns an existing agent surface into a truthful ACP server through acpkit, pydantic-acp, the published docs, and the maintained examples.
---

# ACP Kit SDK

Use this skill when the task is about ACP Kit’s actual SDK surface rather than generic ACP theory.

ACP Kit’s primary job is to take an existing agent surface, usually a `pydantic_ai.Agent`, and expose it as an ACP server boundary without fabricating runtime state the source agent cannot really honor.

This file is the lightweight orchestration entrypoint. The repo-root `SKILL.md` is the longform one-file reference.

Typical triggers:

- `acpkit` CLI target resolution or launch behavior
- `pydantic-acp` runtime behavior, extension seams, or examples
- `codex-auth-helper`
- SDK documentation, examples, and guides that must match the current implementation

## Start Here

Read [resources/intro.md](resources/intro.md) first.

When you need the docs map or the full docs corpus in one place, read `https://vcoderun.github.io/acpkit/llms.txt` or `https://vcoderun.github.io/acpkit/llms-full.txt`.

That file explains:

- what ACP Kit currently ships
- which seam to use (`run_acp`, `create_acp_agent`, providers, bridges, `AgentSource`)
- what the adapter can actually do today
- the current guardrails that often matter in real tasks

## Load Only The References You Need

- Public imports, package map, and canonical names:
  [references/package-surface.md](references/package-surface.md)
- Runtime semantics, session behavior, plans, approvals, slash commands, MCP, projections:
  [references/runtime-capabilities.md](references/runtime-capabilities.md)
- Docs pages, maintained examples, and showcase mapping:
  [references/docs-examples-map.md](references/docs-examples-map.md)

## Utility Scripts

Use the bundled scripts instead of guessing:

- `python3.11 .agents/skills/acpkit-sdk/scripts/list_public_exports.py`
- `python3.11 .agents/skills/acpkit-sdk/scripts/list_examples.py`

## Working Rules

- Prefer current code over stale memory.
- If docs and code disagree, trust code first and update docs.
- Do not invent ACP surface the runtime cannot actually honor.
- Keep examples runnable, explicit, and strongly typed.
- `FileSessionStore` uses `root=Path(...)`.
- Mode slash commands are dynamic, and mode ids must not collide with reserved names such as `model`, `thinking`, `tools`, `hooks`, or `mcp-servers`.
