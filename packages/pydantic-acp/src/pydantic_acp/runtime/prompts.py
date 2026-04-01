from __future__ import annotations as _annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

from acp.schema import (
    AudioContentBlock,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    ResourceContentBlock,
    TextContentBlock,
    Usage,
    UserMessageChunk,
)
from pydantic_ai import AgentRunResult, ModelMessagesTypeAdapter, RunUsage
from pydantic_ai.messages import ModelMessage
from pydantic_ai.tools import DeferredToolRequests

PromptBlock: TypeAlias = (
    AudioContentBlock
    | EmbeddedResourceContentBlock
    | ImageContentBlock
    | ResourceContentBlock
    | TextContentBlock
)

__all__ = (
    "PromptBlock",
    "PromptRunOutcome",
    "build_user_updates",
    "contains_deferred_tool_requests",
    "derive_title",
    "load_message_history",
    "prompt_to_text",
    "usage_from_run",
)


@dataclass(slots=True, kw_only=True)
class PromptRunOutcome:
    result: AgentRunResult[object]
    stop_reason: Literal["cancelled", "end_turn"]


def build_user_updates(
    prompt: list[PromptBlock],
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


def derive_title(prompt: list[PromptBlock]) -> str:
    text = prompt_to_text(prompt).strip()
    if not text:
        return "Untitled session"
    collapsed = " ".join(text.split())
    return collapsed[:80]


def load_message_history(raw_history: str | None) -> list[ModelMessage]:
    if raw_history is None:
        return []
    return ModelMessagesTypeAdapter.validate_json(raw_history)


def contains_deferred_tool_requests(output_type: object) -> bool:
    if output_type is DeferredToolRequests:
        return True
    if isinstance(output_type, Sequence):
        return any(contains_deferred_tool_requests(item) for item in output_type)
    return False


def prompt_to_text(prompt: list[PromptBlock]) -> str:
    parts: list[str] = []
    for block in prompt:
        if isinstance(block, TextContentBlock):
            parts.append(block.text)
        elif isinstance(block, ResourceContentBlock):
            parts.append(f"[resource:{block.name}] {block.uri}")
        elif isinstance(block, EmbeddedResourceContentBlock):
            resource = block.resource
            if hasattr(resource, "text"):
                parts.append(str(resource.text))
            else:
                parts.append(f"[embedded-resource:{resource.uri}]")
        elif isinstance(block, ImageContentBlock):
            parts.append("[image]")
        else:
            parts.append("[audio]")
    return "\n\n".join(parts)


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
