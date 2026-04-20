# LangChain ACP Prompt Resources And Context

`langchain-acp` accepts the same ACP prompt block families as the rest of ACP
Kit, but converts them into LangChain content items instead of Pydantic user
content parts.

Supported prompt block families:

- `TextContentBlock`
- `ResourceContentBlock`
- `EmbeddedResourceContentBlock`
- `ImageContentBlock`
- `AudioContentBlock`

## Conversion Model

The adapter converts ACP blocks into LangChain message content objects.

That conversion is intentionally mechanical. The adapter preserves the prompt
shape the host sent and does not invent extra semantics on top of it.

### Text

- `TextContentBlock` -> `{"type": "text", "text": ...}`

### Images

- `ImageContentBlock` -> `{"type": "image_url", ...}`

The adapter preserves the binary payload as a data URL so the downstream model
path can still receive real image content.

### Audio

- `AudioContentBlock` -> `{"type": "audio", "base64": ..., "mime_type": ...}`

### Resource Links

`ResourceContentBlock` becomes a text description that keeps the URI visible.

Typical rendered fields:

- resource title or name
- URI
- description
- MIME type
- size

### Embedded Resources

Embedded text resources become explicit text content.

Embedded blob resources behave like this:

- image blobs -> image content
- audio blobs -> audio content
- other binary blobs -> explicit text summary, not fake tool calls

## Example

Use resource links when the model only needs the pointer. Use embedded text
resources when the exact attached text must be visible on the next turn.

```python
from acp.schema import EmbeddedResourceContentBlock, ResourceContentBlock, TextContentBlock

prompt = [
    TextContentBlock(text="Summarize the attached file."),
    ResourceContentBlock(uri="file:///workspace/notes.md", title="notes.md"),
    EmbeddedResourceContentBlock(
        text="Use this excerpt as source material.",
    ),
]
```

## What The Adapter Does Not Do

It does not:

- reopen a file just because a resource URI appears
- execute a tool because a diff was attached
- invent extra runtime semantics for `@rule`-style text

Prompt resources stay prompt resources.

## Why This Matters

For editor integrations this means:

- branch diffs can be attached as prompt context
- selected ranges can be attached as prompt context
- file or directory pointers can stay lightweight references
- binary prompt input can survive the ACP boundary when the downstream model
  path supports it
