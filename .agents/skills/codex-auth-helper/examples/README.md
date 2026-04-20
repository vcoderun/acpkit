# codex-auth-helper Examples

The helper package is normally combined with `pydantic-ai` and `pydantic-acp`.

## Runnable Example

```bash
uv run python .agents/skills/codex-auth-helper/examples/codex_responses_agent.py
```

The example:

- builds a `CodexResponsesModel` from the local Codex login
- creates a `pydantic_ai.Agent`
- exposes that agent through `pydantic-acp`

Required local state:

```text
~/.codex/auth.json
```

If the machine is not logged in yet:

```bash
codex login
```
