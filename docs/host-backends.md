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

### HostAccessPolicy

`HostAccessPolicy` adds a typed guardrail surface for host-backed file and terminal access.

```python
from pydantic_acp import ClientHostContext, HostAccessPolicy

host = ClientHostContext.from_session(
    client=client,
    session=session,
    access_policy=HostAccessPolicy(),
    workspace_root=session.cwd,
)
```

### What Problem It Solves

Host-backed integrations usually end up re-implementing the same decisions:

- should absolute paths be allowed
- should paths outside the active session cwd only warn or hard fail
- should workspace-root escapes be blocked
- should command cwd and command path arguments be treated with the same guardrail model

When that logic lives only in one downstream client, ACP-visible warnings and real execution policy drift apart. `HostAccessPolicy` gives ACP Kit one typed surface for both evaluation and enforcement.

Default policy behavior is conservative:

- absolute file paths warn
- paths outside the active session cwd warn
- paths outside the configured workspace root deny
- command cwd escapes and command path targets are evaluated with the same model

When the policy returns `deny`, the client backend raises `PermissionError` before the ACP request is sent.

### Policy Shape

`HostAccessPolicy` currently controls seven decision points:

- `absolute_path`
- `path_outside_cwd`
- `path_outside_workspace`
- `command_cwd_outside_cwd`
- `command_cwd_outside_workspace`
- `command_external_paths`
- `command_paths_outside_workspace`

Each decision point resolves to:

- `allow`
- `warn`
- `deny`

You can also start from named presets:

```python
from pydantic_acp import HostAccessPolicy

strict_policy = HostAccessPolicy.strict()
permissive_policy = HostAccessPolicy.permissive()
```

Use `strict()` when a coding agent should stay tightly inside the declared workspace. Use `permissive()` when the host still wants visibility into risk but does not want ACP Kit to deny as aggressively.

The evaluation objects are intentionally UI-friendly:

```python
evaluation = strict_policy.evaluate_command(
    'python',
    args=['../scripts/build.py'],
    session_cwd=session.cwd,
    workspace_root=session.cwd,
)

print(evaluation.headline)
print(evaluation.message)
print(evaluation.recommendation)
```

This makes it easier for ACP clients and downstream integrations to render one consistent warning surface without rebuilding policy text manually.

Minimal verified path example:

```python
from pathlib import Path

from pydantic_acp import HostAccessPolicy

policy = HostAccessPolicy.strict()
evaluation = policy.evaluate_path(
    '../notes.txt',
    session_cwd=Path('/workspace/app'),
    workspace_root=Path('/workspace/app'),
)

assert evaluation.disposition == 'deny'
assert evaluation.should_deny
assert 'outside_cwd' in evaluation.risk_codes
```

### Evaluation Surfaces

Path evaluation returns `HostPathEvaluation`. Command evaluation returns `HostCommandEvaluation`.

Both surfaces expose:

- `disposition`
- `message`
- `headline`
- `recommendation`
- `risks`
- `risk_codes`
- `primary_risk`
- `has_risks`
- `should_warn`
- `should_deny`
- `summary_lines()`

This split is deliberate:

- `evaluate_*` is for UI, previews, approval cards, or dry-run decisions
- `enforce_*` is for actual blocking behavior before ACP host requests are sent

### File And Command Evaluation Model

File access is evaluated against:

- the active session cwd
- the configured workspace root, if provided
- whether the original input path was absolute

Command access is evaluated against:

- the resolved command cwd
- the configured workspace root, if provided
- path-like command arguments such as `../file.py`, `/tmp/outside.txt`, or `--output=../dist/result.txt`

The current command-path detection is intentionally heuristic. It is designed to catch obvious path targets and drive better guardrails or UI warnings, not to be a full shell parser.

### Recommended Integration Pattern

Use the same policy in two places:

1. host backend enforcement
2. client-side projection or approval UX

That way:

- the warning a user sees
- and the rule that actually blocks execution

come from the same evaluation model.

Example:

```python
policy = HostAccessPolicy.strict()

host = ClientHostContext.from_session(
    client=client,
    session=session,
    access_policy=policy,
    workspace_root=session.cwd,
)

evaluation = policy.evaluate_command(
    'python',
    args=['../scripts/build.py'],
    session_cwd=session.cwd,
    workspace_root=session.cwd,
)
```

### Current Scope And Limits

`HostAccessPolicy` is intentionally narrow today.

It does:

- evaluate file paths
- evaluate command cwd and obvious path-like arguments
- return typed risk information
- enforce `deny` before ACP file or terminal requests are sent

It does not yet:

- rewrite or sanitize commands
- parse full shell syntax
- automatically wire itself through every runtime seam
- replace product-level approval UX

The current value is consistency: integrations can stop rebuilding one-off guardrail logic and use one native ACP Kit surface instead.

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
