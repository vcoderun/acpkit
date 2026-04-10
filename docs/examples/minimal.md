# Minimal Agent

The smallest ACP Kit integration is [`examples/pydantic/static_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/static_agent.py).

```python
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_acp import run_acp

agent = Agent(
    TestModel(custom_output_text="Hello from the static pydantic-acp example."),
    name="static-example",
    system_prompt="Answer directly and keep responses short.",
)


@agent.tool_plain
def describe_adapter_surface() -> str:
    """Summarize the ACP adapter surface that this example exposes."""

    return "\n".join(
        (
            "This example demonstrates:",
            "- a direct Agent instance",
            "- adapter name inference from agent.name",
            "- static run_acp(agent=...) wiring",
        )
    )


run_acp(agent=agent)
```

## Why This Example Matters

It proves a useful baseline:

- the adapter does not require a factory
- the adapter infers `agent_name` from `agent.name` when possible
- ACP wiring can begin with a single `run_acp(agent=...)` call

Use this pattern first. Only move to factories, providers, or bridges when the runtime actually needs them.
