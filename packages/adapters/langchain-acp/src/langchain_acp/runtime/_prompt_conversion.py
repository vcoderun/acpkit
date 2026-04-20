from __future__ import annotations as _annotations

from typing import Any

from acp.schema import (
    AudioContentBlock,
    BlobResourceContents,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    ResourceContentBlock,
    TextContentBlock,
    TextResourceContents,
)

from ..types import AgentPromptBlock

__all__ = ("message_text", "prompt_to_langchain_content")


def prompt_to_langchain_content(prompt: list[AgentPromptBlock]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for block in prompt:
        if isinstance(block, TextContentBlock):
            content.append({"type": "text", "text": block.text})
            continue
        if isinstance(block, ImageContentBlock):
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{block.mime_type};base64,{block.data}"},
                }
            )
            continue
        if isinstance(block, AudioContentBlock):
            content.append(
                {
                    "type": "audio",
                    "base64": block.data,
                    "mime_type": block.mime_type,
                }
            )
            continue
        if isinstance(block, ResourceContentBlock):
            content.append({"type": "text", "text": _format_resource_link(block)})
            continue
        if isinstance(block, EmbeddedResourceContentBlock):
            content.extend(_embedded_resource_content(block))
            continue
        content.append({"type": "text", "text": str(block.model_dump(mode="json"))})
    return content


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    text_chunks: list[str] = []
    for item in content:
        if isinstance(item, str):
            text_chunks.append(item)
            continue
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str):
                text_chunks.append(text)
    return "".join(text_chunks)


def _embedded_resource_content(
    block: EmbeddedResourceContentBlock,
) -> list[dict[str, Any]]:
    resource = block.resource
    if isinstance(resource, TextResourceContents):
        return [{"type": "text", "text": _format_text_resource(resource)}]
    if isinstance(resource, BlobResourceContents):
        mime_type = resource.mime_type or ""
        if mime_type.startswith("image/"):
            return [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{resource.blob}"},
                }
            ]
        if mime_type.startswith("audio/"):
            return [
                {
                    "type": "audio",
                    "base64": resource.blob,
                    "mime_type": mime_type,
                }
            ]
        return [{"type": "text", "text": _format_blob_resource(resource)}]
    return [{"type": "text", "text": f"Embedded resource: {resource.uri}"}]


def _format_resource_link(block: ResourceContentBlock) -> str:
    lines = [f"Resource: {block.title or block.name}", f"URI: {block.uri}"]
    if block.description:
        lines.append(f"Description: {block.description}")
    if block.mime_type:
        lines.append(f"MIME: {block.mime_type}")
    if block.size is not None:
        lines.append(f"Size: {block.size} bytes")
    return "\n".join(lines)


def _format_text_resource(resource: TextResourceContents) -> str:
    lines = [f"Embedded resource: {resource.uri}"]
    if resource.mime_type:
        lines.append(f"MIME: {resource.mime_type}")
    lines.append(resource.text)
    return "\n".join(lines)


def _format_blob_resource(resource: BlobResourceContents) -> str:
    lines = [f"Embedded resource: {resource.uri}"]
    if resource.mime_type:
        lines.append(f"MIME: {resource.mime_type}")
    lines.append(f"Embedded binary payload ({len(resource.blob)} base64 chars)")
    return "\n".join(lines)
