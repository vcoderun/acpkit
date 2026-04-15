from __future__ import annotations as _annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from acp.schema import ToolCallProgress, ToolCallStart, ToolKind

from pydantic_acp.session.state import JsonValue

from ._projection_text import truncate_text

__all__ = ("HookEvent", "HookProjectionMap")

HookProgressStatus = Literal["completed", "failed"]


def _default_event_labels() -> dict[str, str]:
    return {
        "before_model_request": "Before Model",
        "before_node_run": "Before Node",
        "before_run": "Before Run",
        "before_tool_execute": "Before Tool",
        "before_tool_validate": "Before Validate",
        "event": "Event",
        "model_request": "Model Request",
        "model_request_error": "Model Request Error",
        "node_run": "Node Run",
        "node_run_error": "Node Run Error",
        "run": "Run",
        "run_error": "Run Error",
        "run_event_stream": "Run Stream",
        "tool_execute": "Tool Execute",
        "tool_execute_error": "Tool Execute Error",
        "tool_validate": "Tool Validate",
        "tool_validate_error": "Tool Validate Error",
    }


def _default_event_kinds() -> dict[str, ToolKind]:
    return {
        "before_model_request": "fetch",
        "before_node_run": "execute",
        "before_run": "think",
        "before_tool_execute": "execute",
        "before_tool_validate": "execute",
        "event": "execute",
        "model_request": "fetch",
        "model_request_error": "fetch",
        "node_run": "execute",
        "node_run_error": "execute",
        "run": "think",
        "run_error": "think",
        "run_event_stream": "fetch",
        "tool_execute": "execute",
        "tool_execute_error": "execute",
        "tool_validate": "execute",
        "tool_validate_error": "execute",
    }


@dataclass(slots=True, frozen=True, kw_only=True)
class HookEvent:
    event_id: str
    hook_name: str
    tool_name: str | None
    tool_filters: tuple[str, ...]
    raw_output: str | None = None
    status: HookProgressStatus | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class HookProjectionMap:
    title_prefix: str = "Hook"
    event_labels: Mapping[str, str] = field(default_factory=_default_event_labels)
    event_kinds: Mapping[str, ToolKind] = field(default_factory=_default_event_kinds)
    hidden_event_ids: frozenset[str] = frozenset()
    include_raw_input: bool = True
    include_tool_filters: bool = True
    include_raw_output: bool = True
    show_hook_name_in_title: bool = True
    show_tool_name_in_title: bool = True
    max_output_chars: int = 2000

    def build_start_update(
        self,
        *,
        tool_call_id: str,
        event: HookEvent,
    ) -> ToolCallStart | None:
        if event.event_id in self.hidden_event_ids:
            return None
        return ToolCallStart(
            session_update="tool_call",
            tool_call_id=tool_call_id,
            title=self._title(event),
            kind=self._kind(event.event_id),
            status="in_progress",
            raw_input=self._raw_input(event),
        )

    def build_progress_update(
        self,
        *,
        tool_call_id: str,
        event: HookEvent,
    ) -> ToolCallProgress | None:
        if event.event_id in self.hidden_event_ids or event.status is None:
            return None
        return ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id=tool_call_id,
            title=self._title(event),
            kind=self._kind(event.event_id),
            status=event.status,
            raw_output=self._raw_output(event),
        )

    def build_updates(
        self,
        *,
        tool_call_id: str,
        event: HookEvent,
    ) -> tuple[ToolCallStart | None, ToolCallProgress | None]:
        return (
            self.build_start_update(tool_call_id=tool_call_id, event=event),
            self.build_progress_update(tool_call_id=tool_call_id, event=event),
        )

    def _title(self, event: HookEvent) -> str:
        parts = [self.title_prefix, self._label(event.event_id)]
        if self.show_tool_name_in_title and event.tool_name:
            parts.append(f"[{event.tool_name}]")
        title = " ".join(parts)
        if self.show_hook_name_in_title and event.hook_name and event.hook_name != event.event_id:
            return f"{title} ({event.hook_name})"
        return title

    def _label(self, event_id: str) -> str:
        label = self.event_labels.get(event_id)
        if label is not None:
            return label
        return " ".join(part.capitalize() for part in event_id.split("_"))

    def _kind(self, event_id: str) -> ToolKind:
        kind = self.event_kinds.get(event_id)
        if kind is not None:
            return kind
        return "execute"

    def _raw_input(self, event: HookEvent) -> dict[str, JsonValue] | None:
        if not self.include_raw_input:
            return None
        raw_input: dict[str, JsonValue] = {"event": event.event_id}
        if event.hook_name:
            raw_input["hook"] = event.hook_name
        if event.tool_name:
            raw_input["tool_name"] = event.tool_name
        if self.include_tool_filters and event.tool_filters:
            raw_input["tools"] = list(event.tool_filters)
        return raw_input

    def _raw_output(self, event: HookEvent) -> str | None:
        if not self.include_raw_output or event.raw_output is None:
            return None
        return truncate_text(event.raw_output, limit=self.max_output_chars)
