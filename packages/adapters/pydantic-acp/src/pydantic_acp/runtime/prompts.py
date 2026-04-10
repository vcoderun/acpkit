from __future__ import annotations as _annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, TypeAlias

from acp.schema import (
    AudioContentBlock,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    ResourceContentBlock,
    TextContentBlock,
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
    ModelMessage,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
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

__all__ = (
    "PromptBlock",
    "PromptRunOutcome",
    "build_user_updates",
    "build_cancelled_history",
    "contains_deferred_tool_requests",
    "dump_message_history",
    "derive_title",
    "load_message_history",
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
