# pydantic-acp Examples

This directory contains focused examples for the public `pydantic-acp` SDK surface.

- `static_agent.py`
  Smallest `run_acp(agent=...)` setup.
- `factory_agent.py`
  Session-aware factory plus session-local model selection.
- `providers.py`
  Models, modes, config options, plan updates, and approval-state providers.
- `bridges.py`
  `AgentBridgeBuilder`, hooks, history processors, prepare-tools modes, and MCP metadata.
- `approvals.py`
  Native deferred approval flow for approval-gated tools.
- `host_context.py`
  `ClientHostContext` usage for filesystem and terminal access inside a factory-built agent.

All examples use `TestModel` so they stay deterministic and credential-free. Replace `TestModel(...)`
with your real model string or model object when wiring a production agent.
