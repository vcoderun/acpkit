---
title: ACP Kit
---

# ACP Kit {.hide}

--8<-- "docs/.partials/index-header.html"

ACP Kit is the adapter toolkit and monorepo for exposing existing agent runtimes through ACP without inventing runtime behavior the source framework does not actually own.

Today the repo ships two production-grade adapter families:

- [`pydantic-acp`](pydantic-acp.md) for `pydantic_ai.Agent`
- [`langchain-acp`](langchain-acp.md) for LangChain, LangGraph, and DeepAgents graphs

The repo also ships helper packages around those adapters:

- [`codex-auth-helper`](helpers.md) for Codex-backed Responses model construction in Pydantic AI
- [`acpremote`](acpremote.md) for exposing any existing ACP agent or stdio ACP command over
  WebSocket

The helper packages are not adapters. They are adjacent transport or model-construction layers
that support the adapters when you already have a runtime boundary in place.

> ACP Kit adapters are designed for truthful ACP exposure: if the runtime cannot really support a model picker, mode switch, plan state, approval flow, or MCP surface, the adapter does not pretend that it can.

Three ideas drive the SDK:

- truthful ACP exposure instead of optimistic UI surface
- host-owned state through explicit providers and bridges
- runnable examples that map directly to maintained code in [`examples/pydantic/`](https://github.com/vcoderun/acpkit/tree/main/examples/pydantic) and [`examples/langchain/`](https://github.com/vcoderun/acpkit/tree/main/examples/langchain)

## Package Map

| Package | Purpose | Start here |
|---|---|---|
| [`pydantic-acp`](pydantic-acp.md) | production-grade ACP adapter for `pydantic_ai.Agent` | If your runtime starts from a `pydantic_ai.Agent` |
| [`langchain-acp`](langchain-acp.md) | graph-centric ACP adapter for LangChain, LangGraph, and DeepAgents | If your runtime already produces a compiled graph |
| [`acpkit`](cli.md) | CLI target resolution, launch helpers, adapter dispatch | If you want `acpkit run ...` or `acpkit launch ...` |
| [`helpers`](helpers.md) | supporting packages such as `codex-auth-helper` and `acpremote` | If you need transport or model-construction helpers around an adapter |

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

Install the root package with the adapter that matches your runtime:

```bash
uv pip install "acpkit[pydantic]"
```

```bash
uv pip install "acpkit[langchain]"
```

Smallest Pydantic path:

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

Smallest LangChain path:

```python
from langchain.agents import create_agent
from langchain_acp import run_acp

graph = create_agent(model="openai:gpt-5", tools=[])

run_acp(graph=graph)
```

From there you can layer in:

- [Pydantic ACP Overview](pydantic-acp.md) if your runtime starts from `pydantic_ai.Agent`
- [LangChain ACP Overview](langchain-acp.md) if your runtime starts from LangChain, LangGraph, or DeepAgents
- [Pydantic providers](providers.md), [bridges](bridges.md), and [host backends and projections](host-backends.md) for the `pydantic-acp` adapter surface
- [LangChain providers](langchain-acp/providers.md), [bridges](langchain-acp/bridges.md), and [projections](langchain-acp/projections.md) for the `langchain-acp` adapter surface
- [helpers](helpers.md) for supporting packages such as `codex-auth-helper` and `acpremote`

## A Good Reading Order

<div class="callout-grid">
  <div class="callout-panel">
    <h3>New to ACP Kit</h3>
    <p>Start with <a href="getting-started/installation/">Installation</a>, then the <a href="getting-started/quickstart/">quickstart hub</a>, then choose the adapter-specific quickstart that matches your runtime.</p>
  </div>
  <div class="callout-panel">
    <h3>Building a real product integration</h3>
    <p>Read the adapter overview that matches your runtime, then move to providers, bridges, and the maintained examples.</p>
  </div>
</div>

## Why This Adapter Feels Different

Most ACP adapters can stream text. The hard part is preserving the rest of the runtime honestly.

ACP Kit is designed around that harder requirement:

- if a session supports switching models, the adapter exposes model selection
- if a session does not, the adapter does not fake a model picker
- if a plan exists, the ACP plan state is updated and can be resumed
- if a tool call needs approval, ACP permission semantics are preserved
- if the host owns mode state, plan persistence, or config options, that ownership stays explicit

That design keeps the adapter predictable for clients and maintainable for hosts.
