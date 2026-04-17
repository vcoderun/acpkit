# Session-aware Factory

[`examples/pydantic/factory_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/factory_agent.py) is the smallest example that reacts to session context.

```python
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_acp import (
    AcpSessionContext,
    AdapterConfig,
    AdapterModel,
    MemorySessionStore,
    run_acp,
)

FAST_MODEL = TestModel(custom_output_text="Fast workspace summary.")
REVIEW_MODEL = TestModel(custom_output_text="Detailed review summary.")


def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    default_model = REVIEW_MODEL if session.cwd.name == "review" else FAST_MODEL
    return Agent(
        default_model,
        name=f"factory-{session.cwd.name}",
        system_prompt="You are a session-aware ACP example.",
    )


config = AdapterConfig(
    allow_model_selection=True,
    available_models=[
        AdapterModel(model_id="fast", name="Fast", override=FAST_MODEL),
        AdapterModel(model_id="review", name="Review", override=REVIEW_MODEL),
    ],
    session_store=MemorySessionStore(),
)

run_acp(agent_factory=build_agent, config=config)
```

## What It Adds Beyond The Minimal Example

- the active workspace can affect the default model
- session-local model switching is visible through ACP
- you still do not need a custom `AgentSource`

This is the right middle ground when the session matters but the runtime is still simple.

## Validating Session-derived Inputs

In practice, a factory often derives behavior from session-owned inputs such as:

- `session.cwd`
- session-local config values
- host-bound metadata added during session creation

Keep that routing explicit and give it a stable fallback:

```python
from pydantic_acp import AcpSessionContext


def model_for_session(session: AcpSessionContext) -> str:
    workspace_name = session.cwd.name.strip().lower()
    if workspace_name == "review":
        return "review"
    if workspace_name == "":
        return "fast"
    return "fast"
```

Then keep the factory itself simple:

```python
def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    model_id = model_for_session(session)
    default_model = REVIEW_MODEL if model_id == "review" else FAST_MODEL
    return Agent(default_model, name=f"factory-{session.cwd.name or 'workspace'}")
```

That pattern avoids two common problems:

- burying routing logic inside `Agent(...)` construction
- assuming every session-derived input is already clean or recognized
