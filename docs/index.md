---
title: ACP Kit
---

# ACP Kit {.hide}

--8<-- "docs/.partials/index-header.html"

ACP Kit is the adapter toolkit and monorepo for exposing existing agent runtimes through ACP.

Today the stable production focus is [`pydantic-acp`](pydantic-acp.md): an adapter that lets you keep writing normal [`pydantic_ai.Agent`](https://ai.pydantic.dev/agent/) code while exposing ACP-native session state, plans, approvals, slash commands, MCP metadata, and host-backed tooling.

Additional adapters such as `langchain-acp` and `dspy-acp` are planned after `pydantic-acp`
reaches 1.0 stability.

> `pydantic-acp` is designed for truthful ACP exposure: if the runtime cannot really support a model picker, mode switch, plan state, approval flow, or MCP surface, the adapter does not pretend that it can.

Three ideas drive the SDK:

- truthful ACP exposure instead of optimistic UI surface
- host-owned state through explicit providers and bridges
- runnable examples that map directly to maintained code in [`examples/pydantic/`](https://github.com/vcoderun/acpkit/tree/main/examples/pydantic)

## Package Map

| Package | Purpose | Start here |
|---|---|---|
| [`acpkit`](cli.md) | CLI target resolution, launch helpers, adapter dispatch | If you want `acpkit run ...` or `acpkit launch ...` |
| [`pydantic-acp`](pydantic-acp.md) | production-grade ACP adapter for `pydantic_ai.Agent` | If you are exposing agents through ACP today |
| [`codex-auth-helper`](helpers.md) | Codex auth and Responses model factory | If you want Codex-backed models in Pydantic AI |

## What ACP Kit Covers

ACP Kit is not a new agent framework. It sits at the boundary between an existing runtime and ACP clients.

That boundary includes:

- session creation, loading, forking, replay, and close
- session-local model and mode state
- ACP config options and slash commands
- prompt resources such as file refs, directory refs, embedded text selections, branch diffs, images, and audio
- native plan state and provider-backed plan state
- approval workflows and remembered policy metadata
- MCP server metadata and tool classification
- host-backed filesystem and terminal helpers
- projection of reads, writes, and shell commands into ACP-friendly updates

## Quickstart

Install the root package with the Pydantic adapter:

```bash
uv pip install "acpkit[pydantic]"
```

Build a normal Pydantic AI agent and expose it:

```python
from pydantic_ai import Agent
from pydantic_acp import run_acp

agent = Agent(
    "openai:gpt-5",
    name="weather-agent",
    instructions="Answer briefly and ask for clarification when location is missing.",
)


@agent.tool_plain
def lookup_weather(city: str) -> str:
    """Return a canned weather response for demos."""

    return f"Weather in {city}: sunny"


run_acp(agent=agent)
```

From there you can layer in:

- [`AdapterConfig`](pydantic-acp/adapter-config.md) for persistence and runtime wiring
- [prompt resources and context](pydantic-acp/prompt-resources.md) for Zed selections, branch diffs, file refs, and multimodal prompt input
- [providers](providers.md) for host-owned models, modes, config, and approval metadata
- [bridges](bridges.md) for ACP-visible capabilities like thinking, mode-aware tool shaping, hooks, and MCP
- [host backends and projections](host-backends.md) for richer filesystem and terminal UX

## A Good Reading Order

<div class="callout-grid">
  <div class="callout-panel">
    <h3>New to ACP Kit</h3>
    <p>Start with <a href="getting-started/installation/">Installation</a>, then <a href="getting-started/quickstart/">Quickstart</a>, then the <a href="examples/minimal/">minimal example</a>.</p>
  </div>
  <div class="callout-panel">
    <h3>Building a real product integration</h3>
    <p>Read <a href="pydantic-acp.md">Pydantic ACP Overview</a>, <a href="providers.md">Providers</a>, <a href="bridges.md">Bridges</a>, and the <a href="examples/workspace-agent/">workspace agent showcase</a>.</p>
  </div>
</div>

## Why This Adapter Feels Different

Most ACP adapters can stream text. The hard part is preserving the rest of the runtime honestly.

`pydantic-acp` is designed around that harder requirement:

- if a session supports switching models, the adapter exposes model selection
- if a session does not, the adapter does not fake a model picker
- if a plan exists, the ACP plan state is updated and can be resumed
- if a tool call needs approval, ACP permission semantics are preserved
- if the host owns mode state, plan persistence, or config options, that ownership stays explicit

That design keeps the adapter predictable for clients and maintainable for hosts.
