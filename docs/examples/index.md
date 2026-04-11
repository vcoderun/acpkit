# Examples

All maintained examples live under [`examples/pydantic/`](https://github.com/vcoderun/acpkit/tree/main/examples/pydantic).

They are intentionally arranged from smallest surface to broadest runtime.

## Example Ladder

| Example | What it demonstrates |
|---|---|
| [`static_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/static_agent.py) | smallest possible `run_acp(agent=...)` integration |
| [`factory_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/factory_agent.py) | session-aware factory plus session-local model selection |
| [`providers.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/providers.py) | host-owned models, modes, config options, plan state, and approval metadata |
| [`approvals.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/approvals.py) | native deferred approval flow |
| [`bridges.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/bridges.py) | bridge builder, prepare-tools modes, history processors, and MCP metadata |
| [`host_context.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/host_context.py) | `ClientHostContext` and ACP client-backed file/terminal access |
| [`strong_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/strong_agent.py) | full workspace coding-agent integration with Codex-backed models |
| [`strong_agent_v2.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/strong_agent_v2.py) | alternative workspace agent using a conventional provider model |

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

Full workspace agent:

```bash
uv run python -m examples.pydantic.strong_agent
```

That example expects a local Codex login.
