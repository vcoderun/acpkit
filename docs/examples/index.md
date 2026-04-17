# Examples

All maintained examples live under [`examples/pydantic/`](https://github.com/vcoderun/acpkit/tree/main/examples/pydantic).

They are intentionally arranged from the smallest adapter seam to richer ACP-aware runtimes.

## Example Ladder

| Example | What it demonstrates |
|---|---|
| [`static_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/static_agent.py) | smallest possible `run_acp(agent=...)` integration |
| [`factory_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/factory_agent.py) | session-aware factory plus session-local model selection |
| [`providers.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/providers.py) | host-owned models, modes, config options, plan state, and approval metadata |
| [`approvals.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/approvals.py) | native deferred approval flow |
| [`bridges.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/bridges.py) | bridge builder, prepare-tools modes, thread executors, tool metadata, return schemas, and builtin tool projection |
| [`host_context.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/host_context.py) | `ClientHostContext` and ACP client-backed file/terminal access |
| [`strong_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/strong_agent.py) | compact workspace runtime with `ask/plan/agent`, structured native plans, approvals, host tools, and projection maps |
| [`strong_agent_v2.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/strong_agent_v2.py) | media-aware prompt model override provider plus repository-read projection |

## Recommended Reading Order

1. [Minimal Agent](minimal.md)
2. [Session-aware Factory](factory.md)
3. [Provider-backed Session State](providers.md)
4. [Workspace Agent](workspace-agent.md)

## Running The Examples

Minimal examples:

```bash
uv run python -m examples.pydantic.static_agent
uv run python -m examples.pydantic.factory_agent
uv run python -m examples.pydantic.providers
```

Workspace examples:

```bash
uv run python -m examples.pydantic.strong_agent
uv run python -m examples.pydantic.strong_agent_v2
```

Both workspace examples default to `TestModel`, so they run without external credentials. Set the
relevant environment variables only when you want a live model.
