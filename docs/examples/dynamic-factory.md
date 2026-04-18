# Dynamic Factory Agents

Use `agent_factory=` when the ACP session should influence which `pydantic_ai.Agent` gets built.

This is the right seam when the agent is not truly static, for example:

- the workspace path should change the default model
- the session metadata should change instructions or tool visibility
- the session config values should tune behavior such as tone, strictness, or product mode
- different tenants should get different agent names or prompts

## The Function Signature

`agent_factory` is a callable that receives one argument:

```python
from pydantic_acp import AcpSessionContext


def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    ...
```

The return value must be a `pydantic_ai.Agent`. Async factories are also supported:

```python
async def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    ...
```

The parameterization comes from `session`, not from arbitrary extra function arguments.

Useful fields on `AcpSessionContext` include:

- `session.cwd`
- `session.session_id`
- `session.config_values`
- `session.metadata`
- `session.mcp_servers`

## Minimal Dynamic Factory

This example switches model, name, and prompt based on session inputs:

```python
from pydantic_ai import Agent
from pydantic_acp import AcpSessionContext, AdapterConfig, MemorySessionStore, run_acp


def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    workspace_name = session.cwd.name
    requested_tone = str(session.config_values.get("tone", "concise"))
    tenant = str(session.metadata.get("tenant", "general"))

    model_name = "openai:gpt-5.4-mini"
    if workspace_name.endswith("-deep"):
        model_name = "openai:gpt-5.4"

    system_prompt = (
        f"You are working inside `{workspace_name}` for tenant `{tenant}`. "
        f"Respond in a {requested_tone} style."
    )

    return Agent(
        model_name,
        name=f"{tenant}-{workspace_name}",
        system_prompt=system_prompt,
    )


run_acp(
    agent_factory=build_agent,
    config=AdapterConfig(session_store=MemorySessionStore()),
)
```

What this gives you:

- one ACP server entrypoint
- per-session agent instances
- conditional logic without introducing a custom `AgentSource`

## When To Use `agent_factory=` Versus `AgentSource`

Use `agent_factory=` when:

- only the `Agent(...)` instance changes per session
- dependencies can be captured normally in the closure
- you do not need a separate session-specific `deps` construction path

Use `AgentSource` when:

- the agent and its dependencies are built separately
- the ACP client is part of the build path
- host-owned dependencies should be resolved per session
- you need more control than a single factory function provides

## Real Conditional Patterns

### 1. Workspace-based model selection

```python
def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    if session.cwd.name.startswith("fast-"):
        model_name = "openai:gpt-5.4-mini"
    else:
        model_name = "openai:gpt-5.4"
    return Agent(model_name, name="workspace-agent")
```

### 2. Config-driven behavior

```python
def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    review_mode = bool(session.config_values.get("strict_review", False))
    prompt = "Review code aggressively." if review_mode else "Review code pragmatically."
    return Agent("openai:gpt-5.4", system_prompt=prompt)
```

### 3. Metadata-driven tenant routing

```python
def build_agent(session: AcpSessionContext) -> Agent[None, str]:
    tenant = str(session.metadata.get("tenant", "general"))
    if tenant == "finance":
        return Agent("openai:gpt-5.4", name="finance-agent")
    if tenant == "travel":
        return Agent("openai:gpt-5.4-mini", name="travel-agent")
    return Agent("openai:gpt-5.4-mini", name="general-agent")
```

## If You Need Dependencies Too

If the agent also needs session-specific dependencies, step up to `AgentSource`:

```python
from pydantic_acp import AgentSource


class WorkspaceSource(AgentSource[WorkspaceDeps, str]):
    async def get_agent(self, session: AcpSessionContext) -> Agent[WorkspaceDeps, str]:
        return Agent("openai:gpt-5.4", deps_type=WorkspaceDeps)

    async def get_deps(
        self,
        session: AcpSessionContext,
        agent: Agent[WorkspaceDeps, str],
    ) -> WorkspaceDeps:
        del agent
        return WorkspaceDeps(root=session.cwd)
```

Use this only when the plain factory shape is no longer enough.

## Common Mistakes

- trying to pass arbitrary custom parameters directly into `agent_factory`
- using a factory when a single static `Agent(...)` would be simpler
- rebuilding host-owned dependencies inside the factory when `AgentSource.get_deps(...)` is the cleaner seam
- hiding important conditional logic in helpers instead of keeping the routing rules explicit
