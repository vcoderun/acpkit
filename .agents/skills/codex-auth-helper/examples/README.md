# codex-auth-helper Examples

The helper package is normally combined with:

- `pydantic-ai` + `pydantic-acp`
- LangChain + `langchain-acp`

## Runnable Example

```bash
uv run python .agents/skills/codex-auth-helper/examples/codex_responses_agent.py
uv run python .agents/skills/codex-auth-helper/examples/codex_chat_openai_graph.py
```

The example:

- builds a `CodexResponsesModel` from the local Codex login
- creates a `pydantic_ai.Agent`
- exposes that agent through `pydantic-acp`
- builds a LangChain `ChatOpenAI` from the same local Codex login
- creates a LangChain graph
- exposes that graph through `langchain-acp`

Required local state:

```text
~/.codex/auth.json
```

If the machine is not logged in yet:

```bash
codex login
```
