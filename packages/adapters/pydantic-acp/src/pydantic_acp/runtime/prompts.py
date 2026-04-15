from __future__ import annotations as _annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, TypeAlias, cast

from acp.schema import (
    AudioContentBlock,
    BlobResourceContents,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    ResourceContentBlock,
    TextContentBlock,
    TextResourceContents,
    Usage,
    UserMessageChunk,
)
from pydantic_ai import (
    AgentRunResult,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    RunUsage,
    TextPart,
)
from pydantic_ai.messages import (
    AudioUrl,
    BinaryContent,
    BinaryImage,
    DocumentUrl,
    ImageUrl,
    ModelMessage,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserContent,
    UserPromptPart,
)
from pydantic_ai.tools import DeferredToolRequests

PromptBlock: TypeAlias = (
    AudioContentBlock
    | EmbeddedResourceContentBlock
    | ImageContentBlock
    | ResourceContentBlock
    | TextContentBlock
)
PromptInput: TypeAlias = str | list[UserContent]

__all__ = (
    "PromptBlock",
    "PromptInput",
    "PromptRunOutcome",
    "build_user_updates",
    "build_cancelled_history",
    "contains_deferred_tool_requests",
    "dump_message_history",
    "derive_title",
    "load_message_history",
    "prompt_to_input",
    "prompt_to_text",
    "sanitize_message_history",
    "usage_from_run",
)


@dataclass(slots=True, kw_only=True)
class PromptRunOutcome:
    result: AgentRunResult[Any]
    stop_reason: Literal["cancelled", "end_turn"]
    streamed_output: bool = False


def build_user_updates(
    prompt: Sequence[PromptBlock],
    *,
    message_id: str,
) -> list[UserMessageChunk]:
    return [
        UserMessageChunk(
            session_update="user_message_chunk",
            content=block,
            message_id=message_id,
        )
        for block in prompt
    ]


def derive_title(prompt: Sequence[PromptBlock]) -> str:
    text = prompt_to_text(prompt).strip()
    if not text:
        return "Untitled session"
    collapsed = " ".join(text.split())
    return collapsed[:80]


def load_message_history(raw_history: str | None) -> list[ModelMessage]:
    if raw_history is None:
        return []
    return sanitize_message_history(ModelMessagesTypeAdapter.validate_json(raw_history))


def dump_message_history(messages: list[ModelMessage]) -> str:
    return ModelMessagesTypeAdapter.dump_json(messages).decode("utf-8")


def contains_deferred_tool_requests(output_type: Any) -> bool:
    if output_type is DeferredToolRequests:
        return True
    if isinstance(output_type, Sequence):
        return any(contains_deferred_tool_requests(item) for item in output_type)
    return False


def sanitize_message_history(
    messages: list[ModelMessage],
    *,
    error_text: str | None = None,
) -> list[ModelMessage]:
    unresolved_tool_calls = _find_unprocessed_tool_calls(messages)
    if not unresolved_tool_calls:
        return list(messages)

    unresolved_ids = {part.tool_call_id for part in unresolved_tool_calls}
    sanitized_messages: list[ModelMessage] = []
    for message in messages:
        if not isinstance(message, ModelResponse):
            sanitized_messages.append(message)
            continue
        filtered_parts = [
            part
            for part in message.parts
            if not isinstance(part, ToolCallPart) or part.tool_call_id not in unresolved_ids
        ]
        if filtered_parts:
            sanitized_messages.append(replace(message, parts=filtered_parts))

    sanitized_messages.append(
        ModelResponse(
            parts=[
                TextPart(
                    _render_unprocessed_tool_call_text(
                        unresolved_tool_calls,
                        error_text=error_text,
                    )
                )
            ]
        )
    )
    return sanitized_messages


def prompt_to_text(prompt: Sequence[PromptBlock]) -> str:
    parts: list[str] = []
    for block in prompt:
        if isinstance(block, TextContentBlock):
            parts.append(block.text)
        elif isinstance(block, ResourceContentBlock):
            parts.append(_format_resource_link(block.name, block.uri))
        elif isinstance(block, EmbeddedResourceContentBlock):
            resource = block.resource
            if isinstance(resource, TextResourceContents):
                parts.append(_format_text_resource_context(resource.uri, resource.text))
            else:
                parts.append(f"[embedded-resource:{resource.uri}]")
        elif isinstance(block, ImageContentBlock):
            parts.append("[image]")
        else:
            parts.append("[audio]")
    return "\n\n".join(parts)


def prompt_to_input(prompt: Sequence[PromptBlock]) -> PromptInput:
    multimodal_parts: list[UserContent] = []
    has_non_text_blocks = False
    for block in prompt:
        if isinstance(block, TextContentBlock):
            multimodal_parts.append(block.text)
            continue
        has_non_text_blocks = True
        multimodal_parts.append(_prompt_block_to_user_content(block))

    if not has_non_text_blocks:
        return prompt_to_text(prompt)
    return multimodal_parts


def build_error_history(
    raw_history: str | None,
    *,
    prompt_text: str,
    traceback_text: str,
) -> str:
    messages = load_message_history(raw_history)
    stripped_prompt = prompt_text.strip()
    if stripped_prompt:
        messages.append(ModelRequest(parts=[UserPromptPart(stripped_prompt)]))
    messages = sanitize_message_history(messages, error_text=traceback_text)
    if not _history_contains_text(messages, traceback_text):
        messages.append(
            ModelResponse(
                parts=[
                    TextPart(
                        "\n".join(
                            (
                                "The previous run failed before completion.",
                                "",
                                "Traceback:",
                                traceback_text.rstrip(),
                            )
                        )
                    )
                ]
            )
        )
    return dump_message_history(messages)


def build_cancelled_history(
    raw_history: str | None,
    *,
    prompt_text: str,
    details_text: str,
) -> str:
    messages = load_message_history(raw_history)
    stripped_prompt = prompt_text.strip()
    if stripped_prompt:
        messages.append(ModelRequest(parts=[UserPromptPart(stripped_prompt)]))
    messages = sanitize_message_history(messages)
    cancelled_text = _render_cancelled_run_text(details_text)
    if not _history_contains_text(messages, cancelled_text):
        messages.append(ModelResponse(parts=[TextPart(cancelled_text)]))
    return dump_message_history(messages)


def usage_from_run(usage: RunUsage) -> Usage | None:
    if not usage.has_values():
        return None
    thought_tokens = usage.details.get("reasoning_tokens") or usage.details.get("thought_tokens")
    return Usage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_read_tokens=usage.cache_read_tokens or None,
        cached_write_tokens=usage.cache_write_tokens or None,
        thought_tokens=thought_tokens,
        total_tokens=(
            usage.input_tokens
            + usage.output_tokens
            + usage.cache_read_tokens
            + usage.cache_write_tokens
            + usage.input_audio_tokens
            + usage.cache_audio_read_tokens
            + usage.output_audio_tokens
        ),
    )


def _find_unprocessed_tool_calls(messages: list[ModelMessage]) -> list[ToolCallPart]:
    seen_parts: list[ToolCallPart] = []
    resolved_tool_call_ids: set[str] = set()
    for message in messages:
        if isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, ToolCallPart):
                    seen_parts.append(part)
        elif isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, ToolReturnPart | RetryPromptPart):
                    resolved_tool_call_ids.add(part.tool_call_id)
    return [part for part in seen_parts if part.tool_call_id not in resolved_tool_call_ids]


def _prompt_block_to_user_content(
    block: AudioContentBlock
    | EmbeddedResourceContentBlock
    | ImageContentBlock
    | ResourceContentBlock,
) -> UserContent:
    if isinstance(block, ImageContentBlock):
        return BinaryImage(
            data=base64.b64decode(block.data),
            media_type=block.mime_type,
        )
    if isinstance(block, AudioContentBlock):
        return BinaryContent(
            data=base64.b64decode(block.data),
            media_type=block.mime_type,
        )
    if isinstance(block, ResourceContentBlock):
        return _resource_link_to_user_content(block)
    return _embedded_resource_to_user_content(block)


def _resource_link_to_user_content(block: ResourceContentBlock) -> UserContent:
    mime_type = block.mime_type
    if mime_type is None or mime_type.startswith("text/"):
        return _format_resource_link(block.name, block.uri)
    if mime_type.startswith("image/"):
        return ImageUrl(url=block.uri, media_type=mime_type)
    if mime_type.startswith("audio/"):
        return AudioUrl(url=block.uri, media_type=mime_type)
    return DocumentUrl(url=block.uri, media_type=mime_type)


def _embedded_resource_to_user_content(block: EmbeddedResourceContentBlock) -> UserContent:
    resource = block.resource
    if isinstance(resource, TextResourceContents):
        return _format_text_resource_context(resource.uri, resource.text)
    blob_resource = cast(BlobResourceContents, resource)
    return BinaryContent.narrow_type(
        BinaryContent(
            data=base64.b64decode(blob_resource.blob),
            media_type=blob_resource.mime_type or "application/octet-stream",
        )
    )


def _format_resource_link(name: str | None, uri: str) -> str:
    if name:
        return f"[@{name}]({uri})"
    file_name = _resource_name_from_uri(uri)
    if file_name is None:
        return uri
    return f"[@{file_name}]({uri})"


def _format_text_resource_context(uri: str, text: str) -> str:
    return "\n".join(
        (
            _format_resource_link(None, uri),
            f'<context ref="{uri}">',
            text,
            "</context>",
        )
    )


def _resource_name_from_uri(uri: str) -> str | None:
    if uri.startswith("file://"):
        path = uri.removeprefix("file://")
        name = path.split("/")[-1]
        return name or path
    if uri.startswith("zed://"):
        name = uri.split("/")[-1]
        return name or uri
    return None


def _render_unprocessed_tool_call_text(
    tool_calls: list[ToolCallPart],
    *,
    error_text: str | None,
) -> str:
    lines = [
        "One or more tool calls were removed from history because they were not processed.",
        "",
        "Removed tool calls:",
    ]
    for part in tool_calls:
        lines.append(f"- {part.tool_name} ({part.tool_call_id})")
    if error_text:
        lines.extend(("", "Traceback:", error_text.rstrip()))
    return "\n".join(lines)


def _render_cancelled_run_text(details_text: str) -> str:
    return "\n".join(
        (
            "User stopped the run.",
            "",
            "Run details:",
            details_text.rstrip(),
        )
    )


def _history_contains_text(messages: list[ModelMessage], text: str) -> bool:
    needle = text.strip()
    if not needle:
        return False
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        for part in message.parts:
            if isinstance(part, TextPart) and needle in part.content:
                return True
    return False
