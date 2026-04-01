# CLI

The root `acpkit` package currently exposes one command family: `run`.

## Command Shape

```bash
acpkit run TARGET [-p PATH]...
```

`TARGET` can be:

- `module`
- `module:attribute`

`-p/--path` adds extra import roots before module loading. The option can be repeated.

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
acpkit run my_agent
acpkit run my_agent:agent
acpkit run app.agents.demo:agent -p ./examples
acpkit run external_agent -p /absolute/path/to/agents
```

## Related Runtime API

The root package also exports:

- `load_target(...)`
- `run_target(...)`
