# Projection Cookbook

Projection maps should make ACP updates easier to scan.

They should not invent behavior, and they should not dump raw payloads just because the data is available.

## Design Rules

Good projections:

- make tool intent obvious
- separate domain families clearly
- keep titles short and specific
- show high-value locations
- suppress noisy payloads when they do not help

Bad projections:

- mirror raw payloads with no shaping
- give different tool families the same visual shape
- expose internal implementation noise

## Native Helper Primitives

`pydantic-acp` now ships a small public helper surface for integrations that need consistent shaping without rebuilding formatting logic in every client.

High-value helpers:

- `truncate_text(...)`
- `truncate_lines(...)`
- `single_line_summary(...)`
- `format_code_block(...)`
- `format_diff_preview(...)`
- `format_terminal_status(...)`
- `caution_for_path(...)`
- `caution_for_command(...)`

These helpers are intentionally small. They are not a full rendering framework. Their job is to make downstream projection code more consistent and less repetitive.

## What These Helpers Solve

Most integrations repeat the same shaping work:

- shortening long command or output text
- generating a compact one-line command title
- rendering diff previews without raw unified-diff headers
- normalizing terminal status text
- turning path or command risk into one consistent caution message

Those are small problems individually, but they multiply fast across Telegram, Slack, web clients, custom approval cards, and test fixtures.

## Text And Block Helpers

Use:

- `truncate_text(...)` when text length matters more than line structure
- `truncate_lines(...)` when line count matters more than raw character count
- `single_line_summary(...)` for titles, labels, and short command summaries
- `format_code_block(...)` when the client supports Markdown-like code fences

Example:

```python
from pydantic_acp import format_code_block, single_line_summary, truncate_text

title = f"Execute {single_line_summary(command, limit=80)}"
preview = format_code_block(command, language='bash', limit=4000)
stdout = truncate_text(stdout_text, limit=3000)
```

## Diff Helpers

Use `format_diff_preview(...)` when a client needs a compact text diff instead of a structured ACP diff content block.

Example:

```python
from pydantic_acp import format_diff_preview

preview = format_diff_preview(
    'README.md',
    old_text=old_text,
    new_text=new_text,
    max_lines=40,
)
```

Recommended use cases:

- chat clients that need a plain-text diff card
- approval previews
- fallback rendering when the client cannot display structured diff content directly

## Guardrail-Aware Caution Helpers

Use `caution_for_path(...)` and `caution_for_command(...)` when projection code should reflect the same guardrail model as host enforcement.

Example:

```python
from pydantic_acp import HostAccessPolicy, caution_for_command

policy = HostAccessPolicy.strict()
caution = caution_for_command(
    'python',
    args=['../scripts/build.py'],
    session_cwd=session.cwd,
    workspace_root=session.cwd,
    access_policy=policy,
)
```

This matters because the warning a user sees should come from the same evaluation model as the rule that may later deny execution.

## Recommended Projection Strategy

Use these helpers as building blocks:

- policy helpers decide whether a caution banner exists
- text helpers decide how much content to show
- diff helpers shape file changes
- terminal status helpers normalize exit information

That split keeps projection code readable:

- policy logic stays in policy
- rendering logic stays in projection
- truncation and summary rules stay reusable

## File Tools

Use file projections when the tool contract is really:

- read file
- write file
- patch file

Recommended shape:

- title: action plus path
- location: file path
- body: short preview or diff

Do not use a file projection for tools that only incidentally mention a path.

## Command Tools

Use command projections when the tool contract is actually shell or terminal execution.

Recommended shape:

- title: command execution summary
- location: working directory when meaningful
- body: command preview plus output summary

High-value metadata:

- exit code
- signal
- truncation status

## Browser And Navigation Tools

Treat these as a separate domain from file/bash tools.

Recommended shape:

- title: action plus target page or element
- location: page URL or selector
- body: short description of what changed

Useful categories:

- navigate
- inspect
- click
- capture
- analyze

## Scheduler And Background Tools

These should not look like shell commands unless they really are shell commands.

Recommended shape:

- title: task action
- location: task id or queue id
- body: status, timing, next run, or recent result summary

## Subagent And Orchestration Tools

These are usually the easiest to make noisy.

Recommended shape:

- title: spawn, handoff, wait, or summary
- location: subagent id when stable
- body: short status and purpose

Hide low-signal orchestration chatter if it does not help the operator.

## Ask-user And Interactive Tools

These should look visibly different from normal tool execution.

Recommended shape:

- title: user input requested
- body: prompt text and structured choices
- metadata: timeout or response requirement only if useful

## Projection Checklist

Before adding a projection, ask:

- is this a real domain family or just a one-off tool?
- what is the smallest useful title?
- what location would help a user orient quickly?
- what payload is useful, and what payload is noise?
- should this be hidden entirely unless something important happens?

If those answers are unclear, the projection is not ready yet.
