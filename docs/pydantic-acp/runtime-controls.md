# Models, Modes, And Slash Commands

`pydantic-acp` exposes a small ACP control plane on top of normal prompts.

These controls exist to keep session state explicit and inspectable from the client UI.

## Slash Commands

The adapter exposes a small fixed command set plus dynamic mode commands.

### Fixed commands

| Command | Purpose |
|---|---|
| `/model` | Show the current model |
| `/model <provider:model>` | Set the current model |
| `/thinking` | Show the current thinking effort |
| `/thinking <effort>` | Set the current thinking effort |
| `/tools` | List visible tools on the active agent |
| `/hooks` | List registered visible hook callbacks |
| `/mcp-servers` | List MCP servers derived from toolsets and session metadata |

### Dynamic mode commands

Mode commands are registered from the current session’s available modes. If your session exposes:

- `review`
- `execute`

then ACP publishes:

- `/review`
- `/execute`

The adapter no longer hardcodes `ask`, `plan`, and `agent` as global commands. They are only published when those modes actually exist.

Mode ids must remain compatible with slash-command addressing:

- they cannot be empty
- they cannot contain whitespace
- they cannot collide with reserved commands such as `model`, `thinking`, `tools`, `hooks`, or `mcp-servers`
- they should stay specific enough that the command still reads clearly in the UI

## Mode Changes Update ACP State

Mode commands do more than print text.

When a mode changes, the adapter updates:

- current mode state
- ACP config options when the mode is mirrored as a config option
- plan state visibility
- available commands
- session metadata

This is why `/plan` or `/agent` affects the UI surface as well as the next prompt.

## Model Selection

Model selection can be provided by either:

- built-in `available_models`
- a `SessionModelsProvider`

If model selection is enabled, the adapter also mirrors it into ACP config options unless that behavior is disabled or the provider owns it already.

Example built-in model config:

```python
from pydantic_acp import AdapterConfig, AdapterModel

config = AdapterConfig(
    allow_model_selection=True,
    available_models=[
        AdapterModel(
            model_id="fast",
            name="Fast",
            description="Lower latency.",
            override="openai:gpt-5-mini",
        ),
        AdapterModel(
            model_id="smart",
            name="Smart",
            description="More capable model.",
            override="openai:gpt-5",
        ),
    ],
)
```

## Thinking Effort

`ThinkingBridge` exposes a session-local ACP config option named `thinking`.

Supported values:

- `default`
- `off`
- `minimal`
- `low`
- `medium`
- `high`
- `xhigh`

Example:

```python
from pydantic_acp import AdapterConfig, ThinkingBridge

config = AdapterConfig(capability_bridges=[ThinkingBridge()])
```

From the UI:

```text
/thinking high
```

The bridge uses Pydantic AI’s native `Thinking` capability to generate model settings rather than inventing provider-specific request payloads itself.

## Mode-aware Tool Surfaces

The common pattern is:

- `ask`: read-only, inspection-focused
- `plan`: inspect and draft ACP plan state
- `agent`: full tool surface plus plan progress tools

This is usually implemented with `PrepareToolsBridge`.

```python
from pydantic_acp import PrepareToolsBridge, PrepareToolsMode
from pydantic_ai.tools import RunContext, ToolDefinition


def ask_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return [tool_def for tool_def in tool_defs if not tool_def.name.startswith("write_")]


prepare_bridge = PrepareToolsBridge(
    default_mode_id="ask",
    modes=[
        PrepareToolsMode(
            id="ask",
            name="Ask",
            description="Read-only repo inspection.",
            prepare_func=ask_tools,
        ),
        PrepareToolsMode(
            id="plan",
            name="Plan",
            description="Draft ACP plan state.",
            prepare_func=ask_tools,
            plan_mode=True,
        ),
    ],
)
```

## What `/tools` Actually Lists

`/tools` lists currently registered visible tools on the active agent.

Important detail:

- internal ACP tools such as `acp_get_plan` are intentionally hidden from `/tools`
- the list reflects the agent after mode-aware prepare-tools filtering

That makes `/tools` a good debugging surface for “why can the model see this tool right now?”

## What `/mcp-servers` Actually Lists

The MCP server listing is assembled from:

- active agent toolsets
- session MCP server payloads
- bridge-contributed MCP metadata

It is primarily intended as a client-visible observability surface, not as the source of truth for server wiring.

## Common Failure Modes

- `/thinking` does not appear unless a `ThinkingBridge()` is configured
- `/model` only appears when model state is actually available
- mode commands are not global; if a mode is not present in current session state, its slash command is not published
- mode ids like `model` or `thinking` are rejected because they would collide with reserved slash commands
