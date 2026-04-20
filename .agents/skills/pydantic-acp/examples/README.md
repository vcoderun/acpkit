# pydantic-acp Examples

This skill uses the maintained public `examples/pydantic/` tree as the primary
reference set.

## Best Public Examples

- [`examples/pydantic/finance_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/finance_agent.py)
- [`examples/pydantic/travel_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/travel_agent.py)
- [`examples/pydantic/README.md`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/README.md)

## When To Use Which

Use `finance_agent.py` for:

- ACP-native plans
- approvals
- mode-aware tool shaping
- projected file diffs

Use `travel_agent.py` for:

- hook projection
- prompt-model overrides
- media prompt behavior
- approval-gated trip files

## Cross-Package Example

For Codex-backed Pydantic AI plus ACP exposure, use the helper skill example:

- [codex_responses_agent.py](https://github.com/vcoderun/acpkit/blob/main/.agents/skills/codex-auth-helper/examples/codex_responses_agent.py)
