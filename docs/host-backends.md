# Host Backends

`pydantic-acp` provides session-scoped helpers for ACP client-backed filesystem and terminal operations. These helpers are small adapters around ACP client methods and keep the active `session_id` attached to every call.

## Filesystem Backend

Use `ClientFilesystemBackend` when a session-scoped tool or helper needs ACP file access:

```python
from pydantic_acp import ClientFilesystemBackend

backend = ClientFilesystemBackend(client=client, session=session)
response = await backend.read_text_file("notes/todo.txt")
```

Supported methods:

- `read_text_file(...)`
- `write_text_file(...)`

## Terminal Backend

Use `ClientTerminalBackend` for session-scoped terminal operations:

```python
from pydantic_acp import ClientTerminalBackend

backend = ClientTerminalBackend(client=client, session=session)
terminal = await backend.create_terminal("python", args=["-V"])
output = await backend.terminal_output(terminal.terminal_id)
```

Supported methods:

- `create_terminal(...)`
- `terminal_output(...)`
- `release_terminal(...)`
- `wait_for_terminal_exit(...)`
- `kill_terminal(...)`

## Combined Host Context

`ClientHostContext` groups both backends under one session-aware object:

```python
from pydantic_acp import ClientHostContext

host = ClientHostContext.from_session(client=client, session=session)
file_response = await host.filesystem.read_text_file("notes/todo.txt")
terminal = await host.terminal.create_terminal("python", args=["-V"])
```

This helper is useful inside session-aware agent factories where tools need consistent access to both filesystem and terminal helpers.
