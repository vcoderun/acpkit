# Prompt Resources And Context

`pydantic-acp` supports ACP prompt content beyond plain text.

That matters most in editor and coding-agent integrations where the client wants to attach:

- selected file ranges
- branch diffs
- file references
- directory references
- image inputs
- audio inputs
- embedded text or binary resources

The adapter treats these as prompt input, not as hidden tool calls.

## What The Adapter Supports

`pydantic-acp` currently accepts these ACP prompt block families:

- `TextContentBlock`
- `ResourceContentBlock`
- `EmbeddedResourceContentBlock`
- `ImageContentBlock`
- `AudioContentBlock`

Execution behavior is intentionally simple:

- plain text stays plain text
- resource links stay references
- embedded text resources become explicit context blocks
- image and audio blocks become binary prompt parts
- embedded binary resources become binary prompt parts

The adapter does not turn an attached resource into an automatic tool invocation.

If a client attaches a branch diff or a file selection, the model receives that diff or selection as context. The adapter does not run `git diff`, reopen the file, or fetch the resource again just because the prompt referenced it.

## Text Rules Are Just Text

Prompts such as:

```text
@rule write concise code
```

are treated as normal text content unless your own runtime adds additional meaning.

`pydantic-acp` does not define a special ACP primitive for `@rule`. That is deliberate:

- plain textual rules should survive as-is
- the adapter should not invent custom runtime semantics the source agent does not actually expose

## Resource Links

ACP resource links are the lightweight reference form.

Typical examples:

```text
[@acpkit](file:///Users/mert/Desktop/acpkit)
```

```text
[@README.md](file:///Users/mert/Desktop/acpkit/README.md)
```

In ACP terms this is a `ResourceContentBlock`.

`pydantic-acp` behavior:

- text-like links stay text links
- image links become `ImageUrl`
- audio links become `AudioUrl`
- other typed links become `DocumentUrl`

This means a client can attach a file or directory reference without embedding the whole payload.

## Embedded Text Context

The higher-value path for editor integrations is embedded text context.

That is how a client can attach a selected range, file snippet, or generated diff directly into the prompt so the agent does not need another round trip to inspect it.

`pydantic-acp` renders embedded text resources in this form:

```text
[@_hook_capability.py?symbol=wrap_run#L79:79](file:///Users/mert/Desktop/acpkit/packages/adapters/pydantic-acp/src/pydantic_acp/bridges/_hook_capability.py?symbol=wrap_run#L79:79)
<context ref="file:///Users/mert/Desktop/acpkit/packages/adapters/pydantic-acp/src/pydantic_acp/bridges/_hook_capability.py?symbol=wrap_run#L79:79">
    async def wrap_run(
</context>
```

This is the important contract:

- the URI stays visible
- the attached text stays visible
- the model sees one explicit context block, not an opaque placeholder

That makes selections, excerpts, and diffs reviewable and replayable.

## Zed Selections And Branch Diffs

Zed-style attachments fit naturally into the embedded-text-resource path.

Examples:

```text
[@git-diff?base=main](zed:///agent/git-diff?base=main)
<context ref="zed:///agent/git-diff?base=main">
diff --git a/app.py b/app.py
@@ -1 +1 @@
-old
+new
</context>
```

```text
[@thread_executor.py#L54:54](file:///Users/mert/Desktop/acpkit/references/pydantic-ai-latest/pydantic_ai_slim/pydantic_ai/capabilities/thread_executor.py#L54:54)
<context ref="file:///Users/mert/Desktop/acpkit/references/pydantic-ai-latest/pydantic_ai_slim/pydantic_ai/capabilities/thread_executor.py#L54:54">
executor
</context>
```

This is useful in Zed because the editor can attach:

- a branch diff
- a symbol selection
- a selected range
- a file snippet

without requiring the agent to reopen the same context through tools.

The adapter preserves these as prompt context and advertises `embedded_context=True` in ACP prompt capabilities.

## Images, Audio, And Embedded Binary Resources

`pydantic-acp` now also carries binary prompt input into `pydantic-ai` instead of flattening everything to placeholder text.

Current behavior:

- `ImageContentBlock` -> `BinaryContent`
- `AudioContentBlock` -> `BinaryContent`
- embedded blob resources -> `BinaryContent`

That means the ACP boundary can now preserve real binary prompt input when the downstream model provider supports it.

Important limit:

- the adapter can carry the input faithfully
- the provider still decides whether the selected model actually supports image, audio, or document-style inputs

## File And Directory References

ACP does not need a separate "directory prompt primitive" for common editor workflows.

A directory reference such as:

```text
[@acpkit](file:///Users/mert/Desktop/acpkit)
```

is just a resource link.

Use a resource link when:

- you want to point at a file or directory
- you do not want to inline the full content

Use an embedded text resource when:

- you want the model to see exact attached text immediately
- you want to include a selection, snippet, or generated diff

## What This Does Not Do

This resource support is intentionally prompt-oriented.

It does not:

- automatically execute a command because a branch diff was attached
- reopen a file because a file URI was present
- infer tool calls from directory links
- invent ACP semantics that do not exist in the source runtime

If your agent should inspect the live workspace again, that still happens through normal tools or host-backed capabilities. Attached resources are just prompt context.

## Recommended Client Behavior

For ACP clients and editor integrations:

- send plain instructions as text blocks
- send lightweight file or directory pointers as resource links
- send selected ranges, branch diffs, or excerpts as embedded text resources
- send image and audio input only when the target model path can make use of it

For Zed-style integrations specifically:

- use embedded text resources for branch diffs and selected ranges
- keep the source URI intact
- prefer attached context over forcing the model to rediscover the same content through tools

## Current Guarantees

The adapter currently guarantees:

- ACP prompt capabilities advertise `image`, `audio`, and `embedded_context`
- text-only prompts still stay simple strings
- mixed prompts become `pydantic-ai` user-content lists
- embedded text context preserves the `context ref` wrapper
- file and directory links survive as links
- Zed branch diff and selection-style URIs are preserved

That makes prompt attachments practical for editor clients without turning them into a parallel tool system.
