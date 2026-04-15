# Integration Testing

ACP Kit's internal tests are not enough to prove a downstream integration is truthful.

Every real integration should have a small black-box ACP test set that validates the adapter at the session boundary.

## Testing Goal

Prove that:

- ACP methods work end to end
- visible session state matches real runtime state
- approvals, plans, and host backends behave coherently
- session replay and reload do not drift from the original run

## Minimum Scenario Set

### 1. Session Lifecycle

Test:

- `new_session`
- prompt once
- reload the same session
- confirm transcript and session state still exist

This catches broken session storage, replay drift, and missing persistence.

### 2. Approval Roundtrip

Test:

- a tool requires approval
- approval is requested
- approval is granted or denied
- the next visible behavior matches the approval result

If persistent choices are enabled, also verify that remembered approval policy is reused intentionally.

### 3. Host-backed File Flow

Test:

- read a file through the ACP client-backed filesystem
- write a file through the ACP client-backed filesystem
- verify the file result and projection are both truthful

### 4. Host-backed Terminal Flow

Test:

- execute a command through the ACP terminal backend
- wait for completion
- inspect output and exit status
- verify projected output matches the real command result

### 5. Mode And Model Switching

Test:

- switch mode
- verify behavior actually changes
- switch model
- verify session-local model state changes cleanly

Do not stop at "the API returned success". Confirm runtime behavior changed.

### 6. Plan Roundtrip

Test:

- create or update plan state
- persist it
- reload the session
- verify the same plan is still visible

### 7. MCP Metadata Visibility

Test:

- configured tool metadata appears as intended
- host-backed and local tool families are distinguishable
- client-facing labels and categories are not ambiguous

## Recommended Test Style

Prefer:

- black-box ACP method calls
- visible session updates
- persisted session assertions
- end-to-end approval and host-backend flows

## Reusable Harness

`pydantic-acp` now ships a small reusable test surface for downstream integrations:

```python
import asyncio

from pydantic_acp import AdapterConfig, BlackBoxHarness, FileSessionStore

harness = BlackBoxHarness.create(
    agent=my_agent,
    config=AdapterConfig(session_store=FileSessionStore(tmp_path / 'sessions')),
)

session = asyncio.run(harness.new_session(cwd=str(tmp_path)))
response = asyncio.run(harness.prompt_text('Inspect the workspace.', session_id=session.session_id))

assert response.stop_reason == 'end_turn'
assert harness.agent_messages(session_id=session.session_id)
```

### What The Harness Actually Solves

Most downstream integrations need the same proof chain:

- start a session
- send a prompt
- observe visible ACP updates
- resolve approval when required
- verify host-backed file or terminal behavior
- reload the session
- confirm replayed state still matches the original run

Without a shared harness, each integration rebuilds:

- a recording ACP client
- a permission response queue
- message reconstruction logic
- update filtering helpers
- session lifecycle wrappers

That duplication is exactly where proof quality drifts. Some integrations stop at "the prompt returned success" and never verify the ACP-visible transcript, approval behavior, or replay path. `BlackBoxHarness` removes that repeated setup cost.

Use this guide after the integration already has a real adapter construction seam.

If the integration still does not know who owns models, modes, approvals, or host access rules, fix that architecture first. The harness is for proof, not for discovering the design by trial and error.

### What `BlackBoxHarness` Provides

- `BlackBoxHarness.create(...)` to build an ACP adapter plus a recording ACP client in one place
- `initialize()` for protocol negotiation tests
- `new_session(...)` and `load_session(...)` helpers that keep track of the last active session id
- `prompt_text(...)` so black-box tests stay focused on behavior instead of ACP block assembly
- `set_mode(...)` and `set_model(...)` helpers for runtime control tests
- `queue_permission_selected(...)` and `queue_permission_cancelled()` for approval roundtrip tests
- `updates(...)` and `updates_of_type(...)` for visible ACP update inspection
- `agent_messages(...)` for reconstructed final assistant messages across streamed chunks
- `tool_updates(...)` for projected tool activity checks

### Typical Usage Pattern

Use it in this order:

1. Create the harness with the real adapter construction seam you expect downstream users to rely on.
2. Start a session with a real `cwd`.
3. Queue approval outcomes before prompts that should defer.
4. Run one prompt through the ACP boundary.
5. Assert on visible updates, not internal helper calls.
6. Reload the same session and verify replayed state.

Checklist before using the harness:

- the integration already has one real adapter construction seam
- session ownership is already decided
- approvals and host tools are already mapped truthfully
- the test is trying to prove ACP-visible behavior, not helper internals

Example:

```python
import asyncio

from pydantic_acp import AdapterConfig, BlackBoxHarness, FileSessionStore

harness = BlackBoxHarness.create(
    agent_factory=build_agent,
    config=AdapterConfig(session_store=FileSessionStore(tmp_path / 'sessions')),
)

session = asyncio.run(harness.new_session(cwd=str(tmp_path)))
harness.queue_permission_selected('allow_once')
response = asyncio.run(harness.prompt_text('Write the workspace note.'))

assert response.stop_reason == 'end_turn'
assert harness.tool_updates(session_id=session.session_id)
assert harness.agent_messages(session_id=session.session_id)

harness.clear_updates()
asyncio.run(harness.load_session(cwd=str(tmp_path), session_id=session.session_id))
assert harness.agent_messages(session_id=session.session_id)
```

Smaller verified example from the harness test:

```python
session = asyncio.run(harness.new_session(cwd=str(tmp_path)))
harness.queue_permission_selected('allow_once')
response = asyncio.run(harness.prompt_text('Write the workspace note.'))

assert response.stop_reason == 'end_turn'
assert harness.agent_messages(session_id=session.session_id) == ['done']
```

### What It Is Not

`BlackBoxHarness` is not:

- a full replacement for product-level end-to-end tests
- a mock ACP transport stack
- a white-box adapter inspection tool

It is intentionally narrow. Its job is to prove the ACP boundary, not to simulate the entire product around it.

### Recommended Assertions

Prefer assertions on:

- returned ACP stop reason
- emitted `ToolCallStart` / `ToolCallProgress` updates
- approval request behavior
- reconstructed agent messages
- persisted session replay
- host-backed filesystem or terminal side effects

Avoid assertions on:

- private helper call order
- internal runtime helper names
- intermediate objects that ACP clients never see

Use the harness when you want to verify session-bound ACP behavior without rebuilding:

- a recording ACP client
- permission response queueing
- update filtering
- agent message reconstruction
- reload and replay assertions

### Recommended Scenario Ladder

If you are adding ACP support to a real project, start with these harness-backed tests:

1. Session create -> prompt -> reload
2. Approval required -> allow once -> visible completion
3. Approval required -> deny once -> visible denial behavior
4. Host file read/write flow
5. Host terminal execution flow
6. Mode switch changes tool behavior
7. Model switch changes session-local model state
8. Plan survives reload if the integration exposes plan state

Avoid:

- deep mocks of private adapter helpers
- tests that only assert helper call order
- white-box assertions that do not prove ACP behavior

## Practical Definition Of Done

An integration test set is good enough when it proves:

- session state is replayable
- approvals are truthful
- file and terminal host backends work end to end
- model and mode changes are real
- plan state survives reload

If those are not covered, the integration still has a proof gap.

## Related Guides

- [Host Backends and Projections](https://vcoderun.github.io/acpkit/host-backends/)
- [Projection Cookbook](https://vcoderun.github.io/acpkit/projection-cookbook/)
- [Compatibility Manifest Guide](https://vcoderun.github.io/acpkit/compatibility-matrix-template/)
