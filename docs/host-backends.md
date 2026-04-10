# Host Backends And Projections

ACP Kit includes two small but important host-facing surfaces:

1. **session-scoped host backends**
2. **projection maps**

The first lets tools talk to the bound ACP client cleanly.
The second makes ACP updates look better in the UI.

## ClientFilesystemBackend

`ClientFilesystemBackend` is a thin adapter over ACP file APIs that automatically carries the active session id.

```python
from pydantic_acp import ClientFilesystemBackend

backend = ClientFilesystemBackend(client=client, session=session)
response = await backend.read_text_file("notes/todo.txt")
print(response.content)
```

Supported methods:

- `read_text_file(...)`
- `write_text_file(...)`

## ClientTerminalBackend

`ClientTerminalBackend` does the same for ACP terminal operations:

```python
from pydantic_acp import ClientTerminalBackend

backend = ClientTerminalBackend(client=client, session=session)
terminal = await backend.create_terminal("python", args=["-V"])
await backend.wait_for_terminal_exit(terminal.terminal_id)
output = await backend.terminal_output(terminal.terminal_id)
print(output.output)
```

Supported methods:

- `create_terminal(...)`
- `terminal_output(...)`
- `release_terminal(...)`
- `wait_for_terminal_exit(...)`
- `kill_terminal(...)`

## ClientHostContext

`ClientHostContext` groups both backends into one session-scoped object:

```python
from pydantic_acp import ClientHostContext

host = ClientHostContext.from_session(client=client, session=session)
file_response = await host.filesystem.read_text_file("notes/workspace.md")
terminal = await host.terminal.create_terminal("python", args=["-V"])
```

This is the most ergonomic option inside a session-aware factory or `AgentSource`.

## Projection Maps

Projection maps do not change tool execution. They change how ACP renders the resulting updates.

### FileSystemProjectionMap

Use this for tool families that correspond to file reads, file writes, or shell commands:

```python
from pydantic_acp import FileSystemProjectionMap

projection = FileSystemProjectionMap(
    read_tool_names=frozenset({"mcp_repo_read_file", "mcp_host_read_workspace_file"}),
    write_tool_names=frozenset({"mcp_host_write_workspace_file"}),
    bash_tool_names=frozenset({"mcp_host_run_command"}),
)
```

This lets ACP clients render:

- read tools as diff-like previews
- write tools as file diffs
- shell tools as command previews or terminal references

### Composing Projection Maps

Multiple projection maps can be combined:

```python
from pydantic_acp import compose_projection_maps

projection_map = compose_projection_maps(filesystem_projection, hook_projection)
```

In practice, most setups pass them through `AdapterConfig.projection_maps`.

## When To Use Host Backends

Use host backends when the ACP client should remain the authority for filesystem or shell access.

That is the right design for:

- editor integrations
- workspace-local coding agents
- security-reviewed command execution flows
- clients that want full visibility into shell creation and release
