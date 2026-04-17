# Workspace Agent

The maintained workspace showcase is [`examples/pydantic/strong_agent.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/strong_agent.py).

This example is intentionally smaller than a production coding agent. The goal is to show the ACP
seams that matter without making agent exposure look heavier than it is.

## What It Shows

`strong_agent.py` demonstrates a compact workspace runtime where ACP owns the session surface while
the underlying Pydantic AI agent still looks like an ordinary `Agent(...)`.

The example keeps only the seams that pay for themselves:

- `PrepareToolsBridge` for `ask`, `plan`, and `agent` modes
- structured native plan generation in plan mode
- plan progress tools in agent mode
- `NativeApprovalBridge` for host-backed writes and command execution
- `FileSystemProjectionMap` so clients can render file and shell activity well
- file-backed session storage and file-backed plan persistence
- `ClientHostContext` for ACP client-backed filesystem and terminal access

## Runtime Shape

The runtime is assembled from one session-aware `AgentSource` plus a small `AdapterConfig`.

Mode shaping is the main ACP seam:

```python
PrepareToolsBridge(
    default_mode_id="ask",
    default_plan_generation_type="structured",
    modes=[
        PrepareToolsMode(
            id="ask",
            name="Ask",
            description="Read-only repository and workspace inspection.",
            prepare_func=_read_only_tools,
        ),
        PrepareToolsMode(
            id="plan",
            name="Plan",
            description="Inspect the workspace and return a structured ACP plan.",
            prepare_func=_read_only_tools,
            plan_mode=True,
        ),
        PrepareToolsMode(
            id="agent",
            name="Agent",
            description="Allow host-backed writes and command execution.",
            prepare_func=_all_tools,
            plan_tools=True,
        ),
    ],
)
```

This keeps the ACP surface truthful:

- `ask` exposes inspection tools only
- `plan` keeps inspection tools and expects a structured native plan
- `agent` restores host-backed mutation tools and plan progress tools

## Why This Example Is Smaller

Older workspace examples tried to demonstrate every seam at once. That makes the runtime look more
expensive than it really is and hides the important ACP boundaries.

The current example avoids:

- custom model registries
- verbose MCP metadata wrappers
- product-specific helper stacks
- provider layers that are not needed for the core story

That keeps the file focused on the adapter seams most users actually need first.

## Host Tools And Projection

The example always exposes local repository tools and only adds host-backed tools when the current
session is bound to a real ACP client:

- `search_repo_paths`
- `read_repo_file`
- `read_workspace_note`
- `write_workspace_note`
- `run_workspace_command`

`FileSystemProjectionMap` is configured with those tool names so ACP clients can render reads,
writes, and shell execution as intentional UI events instead of plain raw output.

## Plan Persistence

The example persists ACP-native plan state to `.acpkit/plans/<session-id>.md`.

That gives you both:

- ACP-visible native plan updates
- a small host-owned artifact you can inspect or archive outside the transcript

The adapter still remains the source of truth for plan state. The file is only a persistence mirror.

## Running It

```bash
uv run python -m examples.pydantic.strong_agent
```

The default model is `TestModel`, so the example runs without external credentials.

When you want a live model, set:

```bash
export ACP_WORKSPACE_MODEL=openai:gpt-5.4-mini
```

## Related Example

[`examples/pydantic/strong_agent_v2.py`](https://github.com/vcoderun/acpkit/blob/main/examples/pydantic/strong_agent_v2.py)
keeps the runtime small but focuses on a different seam: prompt-model overrides for image and audio
prompts.
