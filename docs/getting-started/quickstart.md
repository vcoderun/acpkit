# Quickstart

ACP Kit has two primary adapter entry paths:

- `pydantic-acp` for `pydantic_ai.Agent`
- `langchain-acp` for LangChain, LangGraph, and DeepAgents graphs

The repo also ships helper packages around those adapters:

- `acpremote` for existing ACP agents and stdio ACP commands
- `codex-auth-helper` for Codex-backed Pydantic AI or LangChain model construction

Those helper packages do not replace the adapters. They sit around them:

- `acpremote` moves ACP boundaries across WebSocket transport
- `codex-auth-helper` builds a Codex-backed Responses model for `pydantic-ai` or a Codex-backed `ChatOpenAI` for LangChain

Choose the path that matches the runtime you already have.

## Pydantic AI Path

Use this when your integration starts from a normal `pydantic_ai.Agent`.

- [Pydantic Quickstart](pydantic-quickstart.md)
- [Pydantic ACP Overview](../pydantic-acp.md)
- [Finance Agent example](../examples/finance.md)

## LangChain And LangGraph Path

Use this when your integration starts from:

- `langchain.agents.create_agent(...)`
- a compiled LangGraph graph
- a DeepAgents graph built with `create_deep_agent(...)`

- [LangChain Quickstart](langchain-quickstart.md)
- [LangChain ACP Overview](../langchain-acp.md)
- [Codex-backed LangChain example](../examples/langchain-codex.md)
- [LangChain Workspace Graph example](../examples/langchain-workspace.md)
- [DeepAgents Compatibility Example](../examples/deepagents.md)

## Shared Next Steps

After the adapter-specific quickstart, the next useful ACP Kit seams are usually:

- [acpremote Overview](../acpremote.md) if you need to expose an existing ACP server remotely
- [Helpers](../helpers.md) for the helper package map
- [CLI](../cli.md) for `acpkit run ...` and `acpkit launch ...`
- [Pydantic Providers](../providers.md) if you are integrating `pydantic-acp`
- [Pydantic Bridges](../bridges.md) if you are integrating `pydantic-acp`
- [Pydantic Host Backends and Projections](../host-backends.md) for `pydantic-acp`
- [LangChain Providers](../langchain-acp/providers.md) if you are integrating `langchain-acp`
- [LangChain Bridges](../langchain-acp/bridges.md) if you are integrating `langchain-acp`
- [LangChain Projections and Event Projection Maps](../langchain-acp/projections.md) for `langchain-acp`
