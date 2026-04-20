from __future__ import annotations as _annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    ContentToolCallContent,
    SessionInfoUpdate,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    UserMessageChunk,
)

from .session.state import JsonValue, SessionTranscriptUpdate

__all__ = (
    "CompositeEventProjectionMap",
    "EventProjectionMap",
    "StructuredEventProjectionMap",
    "compose_event_projection_maps",
)

_EVENT_KEYS = frozenset({"acp_events", "callback_events", "events"})


class EventProjectionMap(Protocol):
    def project_event_payload(
        self,
        payload: JsonValue,
    ) -> tuple[SessionTranscriptUpdate, ...] | None: ...


@dataclass(slots=True, frozen=True, kw_only=True)
class CompositeEventProjectionMap:
    maps: tuple[EventProjectionMap, ...]

    def project_event_payload(
        self,
        payload: JsonValue,
    ) -> tuple[SessionTranscriptUpdate, ...] | None:
        updates: list[SessionTranscriptUpdate] = []
        for projection_map in self.maps:
            projected = projection_map.project_event_payload(payload)
            if projected is not None:
                updates.extend(projected)
        return tuple(updates) or None


def compose_event_projection_maps(
    projection_maps: Sequence[EventProjectionMap] | None,
) -> EventProjectionMap | None:
    if projection_maps is None:
        return None
    if len(projection_maps) == 0:
        return None
    if len(projection_maps) == 1:
        return projection_maps[0]
    return CompositeEventProjectionMap(maps=tuple(projection_maps))


@dataclass(slots=True, frozen=True, kw_only=True)
class StructuredEventProjectionMap:
    event_keys: frozenset[str] = _EVENT_KEYS

    def project_event_payload(
        self,
        payload: JsonValue,
    ) -> tuple[SessionTranscriptUpdate, ...] | None:
        event_payloads = _extract_event_payloads(payload, event_keys=self.event_keys)
        if event_payloads is None:
            return None
        projected: list[SessionTranscriptUpdate] = []
        for event_payload in event_payloads:
            projected_update = _event_payload_to_update(event_payload)
            if projected_update is not None:
                projected.append(projected_update)
        return tuple(projected) or None


def _extract_event_payloads(
    payload: JsonValue,
    *,
    event_keys: frozenset[str],
) -> tuple[dict[str, JsonValue], ...] | None:
    if isinstance(payload, list):
        projected_payloads: list[dict[str, JsonValue]] = []
        for item in payload:
            if _is_string_keyed_json_object(item):
                projected_payloads.append(cast(dict[str, JsonValue], item))
        return tuple(projected_payloads) or None
    if not _is_string_keyed_json_object(payload):
        return None
    payload_object = cast(dict[str, JsonValue], payload)
    for key in event_keys:
        candidate = payload_object.get(key)
        if isinstance(candidate, list):
            nested_payloads: list[dict[str, JsonValue]] = []
            for item in candidate:
                if _is_string_keyed_json_object(item):
                    nested_payloads.append(cast(dict[str, JsonValue], item))
            return tuple(nested_payloads) or None
    if _resolve_session_update_kind(payload_object) is not None:
        return (payload_object,)
    return None


def _event_payload_to_update(
    payload: dict[str, JsonValue],
) -> SessionTranscriptUpdate | None:
    normalized = _normalize_event_payload(payload)
    session_update = _resolve_session_update_kind(normalized)
    if session_update is None:
        return None
    if session_update == "agent_message_chunk":
        content = _normalize_text_content(normalized.get("content"))
        if content is None:
            return None
        return AgentMessageChunk(
            session_update="agent_message_chunk",
            content=content,
            message_id=_optional_string(normalized.get("messageId")),
        )
    if session_update == "user_message_chunk":
        content = _normalize_text_content(normalized.get("content"))
        if content is None:
            return None
        return UserMessageChunk(
            session_update="user_message_chunk",
            content=content,
            message_id=_optional_string(normalized.get("messageId")),
        )
    if session_update == "tool_call":
        try:
            return ToolCallStart.model_validate(normalized)
        except Exception:
            return None
    if session_update == "tool_call_update":
        if isinstance(normalized.get("content"), str):
            normalized = dict(normalized)
            normalized["content"] = [
                ContentToolCallContent(
                    type="content",
                    content=TextContentBlock(type="text", text=cast(str, normalized["content"])),
                ).model_dump(mode="json", by_alias=True)
            ]
        try:
            return ToolCallProgress.model_validate(normalized)
        except Exception:
            return None
    if session_update == "session_info_update":
        try:
            return SessionInfoUpdate.model_validate(normalized)
        except Exception:
            return None
    if session_update == "plan":
        try:
            return AgentPlanUpdate.model_validate(normalized)
        except Exception:
            return None
    return None  # pragma: no cover


def _normalize_event_payload(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    normalized = dict(payload)
    if "sessionUpdate" not in normalized:
        if "session_update" in normalized and isinstance(normalized["session_update"], str):
            normalized["sessionUpdate"] = normalized.pop("session_update")
        elif "type" in normalized and isinstance(normalized["type"], str):
            normalized["sessionUpdate"] = normalized["type"]
    return normalized


def _resolve_session_update_kind(payload: dict[str, JsonValue]) -> str | None:
    session_update = payload.get("sessionUpdate")
    if isinstance(session_update, str) and session_update in {
        "agent_message_chunk",
        "plan",
        "session_info_update",
        "tool_call",
        "tool_call_update",
        "user_message_chunk",
    }:
        return session_update
    return None


def _normalize_text_content(value: JsonValue) -> TextContentBlock | None:
    if isinstance(value, str):
        return TextContentBlock(type="text", text=value)
    if _is_string_keyed_json_object(value):
        try:
            return TextContentBlock.model_validate(value)
        except Exception:
            return None
    return None


def _optional_string(value: JsonValue) -> str | None:
    return value if isinstance(value, str) else None


def _is_string_keyed_json_object(value: JsonValue) -> bool:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)
