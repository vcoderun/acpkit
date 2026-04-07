# Helpers

ACP Kit also contains small helper packages that are useful around the adapter runtime but are not part of the root `acpkit` CLI.

## codex-auth-helper

`codex-auth-helper` lives under `packages/helpers/codex-auth-helper/`.

It reads `~/.codex/auth.json`, refreshes tokens when needed, builds a Codex-configured `AsyncOpenAI` client, and returns a ready-to-use `pydantic-ai` Responses model.

Public entry points:

- `create_codex_responses_model(...)`
- `create_codex_async_openai(...)`
- `CodexResponsesModel`
- `CodexAsyncOpenAI`
- `CodexAuthConfig`

Minimal usage:

```python
from codex_auth_helper import create_codex_responses_model
from pydantic_ai import Agent

agent = Agent(create_codex_responses_model("gpt-5"))
```

Important constraints:

- it expects an existing local Codex login
- it reads `~/.codex/auth.json` by default
- it only supports `OpenAIResponsesModel` style usage
- it does not expose `OpenAIChatModel` support

Typical ACP-side usage:

```python
from pydantic_ai import Agent
from codex_auth_helper import create_codex_responses_model
from pydantic_acp import run_acp

agent = Agent(create_codex_responses_model("gpt-5"), name="codex-agent")
run_acp(agent=agent)
```

For the package-level usage guide, see `packages/helpers/codex-auth-helper/README.md`.
