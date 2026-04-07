# CLI

The root `acpkit` package exposes two command families:

- `run`
- `launch`

## Command Shape

```bash
acpkit run TARGET [-p PATH]...
acpkit launch TARGET [-p PATH]...
acpkit launch --command "python3.11 strong_agent.py"
```

`TARGET` can be:

- `module`
- `module:attribute`

`-p/--path` adds extra import roots before module loading. The option can be repeated.

For `launch`, exactly one of `TARGET` or `--command` must be provided. `-p/--path` is only valid
with `TARGET`.

The CLI is implemented with `click`.

## Target Resolution

`acpkit` resolves targets in this order:

1. add the current working directory to `sys.path`
2. add any `-p/--path` roots
3. import the requested module
4. if `module:attribute` was given, resolve the attribute path
5. if only `module` was given, select the last defined `pydantic_ai.Agent` instance in that module

## Supported Agent Detection

Today the root package only auto-dispatches `pydantic_ai.Agent` instances. Detection is runtime-based and uses `isinstance(value, pydantic_ai.Agent)`.

If the resolved value is not a supported agent type, `acpkit` raises `UnsupportedAgentError`.

If the required adapter extra is not installed, `acpkit` raises `MissingAdapterError` and prints an install hint such as:

```bash
uv pip install "acpkit[pydantic]"
```

## Examples

```bash
acpkit run strong_agent
acpkit run strong_agent:agent
acpkit run app.agents.demo:agent -p ./examples
acpkit run external_agent -p /absolute/path/to/agents
acpkit launch strong_agent
acpkit launch strong_agent:agent -p ./examples
acpkit launch --command "python3.11 strong_agent.py"
```

## Launch Semantics

`acpkit launch` is a convenience wrapper around Toad ACP.

Target mode mirrors the resolved target through the root runtime:

```bash
acpkit launch strong_agent:agent -p ./examples
```

This becomes:

```bash
toad acp "acpkit run strong_agent:agent -p ./examples"
```

Raw command mode skips target resolution:

```bash
acpkit launch --command "python3.11 strong_agent.py"
```

This becomes:

```bash
toad acp "python3.11 strong_agent.py"
```

The command is launched through `uvx --python 3.14 --from batrachian-toad ...`, so Toad runs in a
separate Python 3.14 tool environment. Install the helper runtime with:

```bash
uv pip install "acpkit[launch]"
```

## Related Runtime API

The root package also exports:

- `load_target(...)`
- `launch_command(...)`
- `launch_target(...)`
- `run_target(...)`
