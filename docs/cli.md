# CLI

The root `acpkit` package exposes two command families:

- `run`
- `launch`

`run` resolves a Python target and starts the matching ACP adapter directly.
`launch` wraps that target for Toad ACP.

## Command Shapes

```bash
acpkit run TARGET [-p PATH]...
acpkit launch TARGET [-p PATH]...
acpkit launch --command "python3.11 strong_agent.py"
```

`TARGET` can be:

- `module`
- `module:attribute`

`-p/--path` adds extra import roots before module loading and may be repeated.

## How Target Resolution Works

`acpkit` resolves targets in this order:

1. add the current working directory to `sys.path`
2. add any `-p/--path` roots
3. import the requested module
4. if `module:attribute` was given, resolve the attribute path
5. if only `module` was given, select the last defined `pydantic_ai.Agent` in that module

Today the built-in auto-dispatch path supports `pydantic_ai.Agent`.

If the resolved value is not a supported agent type, `acpkit` raises `UnsupportedAgentError`.

## `acpkit run`

Use `run` when the target itself should be resolved and exposed through ACP:

```bash
acpkit run strong_agent
acpkit run strong_agent:agent
acpkit run app.agents.demo:agent -p ./examples
acpkit run external_agent -p /absolute/path/to/agents
```

This is the most direct path from Python code to a running ACP server.

The expected Python module shape is:

```python
from pydantic_ai import Agent
from pydantic_acp import run_acp

agent = Agent("openai:gpt-5", name="demo-agent")

run_acp(agent=agent)
```

That means:

- `acpkit run my_agent_module`
  - imports `my_agent_module`
  - finds the last defined `pydantic_ai.Agent`
  - exposes it through the matching ACP adapter
- `acpkit run my_agent_module:agent`
  - imports the explicit `agent` symbol and exposes that one

If you need adapter configuration such as session persistence, plan bridges, approvals, or host
projection maps, keep that wiring inside the target module and still run the module through
`acpkit run`.

## CLI Versus Runtime API

Use the root CLI when:

- you want target resolution from a module path
- your editor or launcher shells out to a command
- ACP transport lifecycle should be owned by the `acpkit` process

Use `run_acp(...)` when:

- you already have the Python agent object in-process
- the module itself should start the ACP server directly

Use `create_acp_agent(...)` when:

- another runtime should own transport lifecycle
- you want the ACP-compatible agent object without starting the server immediately

## `acpkit launch`

Use `launch` when you want Toad ACP to launch the command for you:

```bash
acpkit launch strong_agent
acpkit launch strong_agent:agent -p ./examples
```

This mirrors the resolved target through:

```bash
toad acp "acpkit run TARGET [-p PATH]..."
```

Raw command mode skips target resolution entirely:

```bash
acpkit launch --command "python3.11 strong_agent.py"
```

That becomes:

```bash
toad acp "python3.11 strong_agent.py"
```

## Installation Hints And Failure Modes

If the matching adapter extra is not installed, `acpkit` raises `MissingAdapterError` and prints an install hint such as:

```bash
uv pip install "acpkit[pydantic]"
```

Common failure cases:

- the module imports but contains no detectable supported agent
- `module:attribute` points at a non-agent object
- the requested adapter package is not installed
- the target module starts ACP with `run_acp(...)` but imports fail before the agent is created

## Runtime API

The root package also exports lower-level runtime helpers:

- `load_target(...)`
- `run_target(...)`
- `launch_target(...)`
- `launch_command(...)`

These are useful when your product needs the same target resolution behavior but cannot shell out to the CLI.
