# Quickstart

This guide takes you from a normal `pydantic_ai.Agent` to a useful ACP server in a few minutes.

## 1. Write A Normal Agent

Start with plain Pydantic AI code:

```python
from pydantic_ai import Agent

agent = Agent(
    "openai:gpt-5",
    name="demo-agent",
    instructions="Answer directly and keep responses short.",
)


@agent.tool_plain
def describe_project() -> str:
    """Return a short summary of the current project."""

    return "This is a demo ACP-enabled project."
```

Nothing here is ACP-specific yet.

## 2. Expose It Through ACP

Wrap the agent with `run_acp(...)`:

```python
from pydantic_acp import run_acp

run_acp(agent=agent)
```

That is the smallest supported integration.

Equivalent one-file example:

```python
from pydantic_ai import Agent
from pydantic_acp import run_acp

agent = Agent(
    "openai:gpt-5",
    name="demo-agent",
    instructions="Answer directly and keep responses short.",
)


@agent.tool_plain
def describe_project() -> str:
    """Return a short summary of the current project."""

    return "This is a demo ACP-enabled project."


if __name__ == "__main__":
    run_acp(agent=agent)
```

## 3. Add Session Persistence

ACP sessions become more useful when they survive process restarts:

```python
from pathlib import Path

from pydantic_acp import AdapterConfig, FileSessionStore, run_acp

run_acp(
    agent=agent,
    config=AdapterConfig(
        session_store=FileSessionStore(root=Path(".acp-sessions")),
    ),
)
```

At this point you already have:

- session creation and replay
- ACP transcript persistence
- message history persistence
- load, fork, resume, and close behavior

## 4. Offer Session-local Models

If you want the UI to expose a model picker, do it explicitly:

```python
from pydantic_acp import AdapterConfig, AdapterModel

config = AdapterConfig(
    allow_model_selection=True,
    available_models=[
        AdapterModel(
            model_id="fast",
            name="Fast",
            description="Lower latency responses.",
            override="openai:gpt-5-mini",
        ),
        AdapterModel(
            model_id="smart",
            name="Smart",
            description="More deliberate responses.",
            override="openai:gpt-5",
        ),
    ],
)
```

The adapter will only show the selector when the configuration supports it.

## 5. Add Modes, Plans, And Thinking

ACP Kit’s richer UX comes from bridges:

```python
from pydantic_acp import (
    AdapterConfig,
    PrepareToolsBridge,
    PrepareToolsMode,
    ThinkingBridge,
)
from pydantic_ai.tools import RunContext, ToolDefinition


def ask_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return [tool_def for tool_def in tool_defs if not tool_def.name.startswith("write_")]


def agent_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return list(tool_defs)


config = AdapterConfig(
    capability_bridges=[
        ThinkingBridge(),
        PrepareToolsBridge(
            default_mode_id="ask",
            modes=[
                PrepareToolsMode(
                    id="ask",
                    name="Ask",
                    description="Read-only inspection mode.",
                    prepare_func=ask_tools,
                ),
                PrepareToolsMode(
                    id="plan",
                    name="Plan",
                    description="Draft ACP plan state.",
                    prepare_func=ask_tools,
                    plan_mode=True,
                ),
                PrepareToolsMode(
                    id="agent",
                    name="Agent",
                    description="Full tool surface.",
                    prepare_func=agent_tools,
                    plan_tools=True,
                ),
            ],
        ),
    ],
)
```

This enables:

- mode switching in ACP
- native plan tools in `plan` mode
- plan progress tools in `agent` mode
- a session-local thinking effort selector

## 6. Run Through The CLI

If you installed the root package:

```bash
acpkit run my_agent_module
acpkit run my_agent_module:agent
acpkit run my_agent_module:agent -p ./examples
```

`acpkit` resolves the target, detects the last defined `pydantic_ai.Agent` when needed, and dispatches it to the installed adapter.

## 7. Know What To Read Next

- Want the runtime architecture? Read [Pydantic ACP Overview](../pydantic-acp.md).
- Want every `AdapterConfig` knob? Read [AdapterConfig](../pydantic-acp/adapter-config.md).
- Want host-owned session state? Read [Providers](../providers.md).
- Want ACP-visible capabilities? Read [Bridges](../bridges.md).
- Want a full coding-agent setup? Read [Workspace Agent](../examples/workspace-agent.md).
