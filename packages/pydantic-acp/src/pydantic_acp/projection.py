from __future__ import annotations as _annotations

from dataclasses import dataclass
from typing import Protocol

from acp.schema import ToolCallLocation, ToolCallProgress, ToolCallStart, ToolKind
from pydantic_ai import ModelMessage, ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart
from typing_extensions import TypeIs

from .serialization import OutputSerializer

__all__ = (
    "DefaultToolClassifier",
    "ToolClassifier",
    "build_tool_updates",
    "extract_tool_call_locations",
)

_PATH_KEYS = (
    "destination_path",
    "file_path",
    "filepath",
    "path",
    "source_path",
    "target_path",
)


def _is_string_keyed_object_dict(value: object) -> TypeIs[dict[str, object]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


class ToolClassifier(Protocol):
    def classify(self, tool_name: str, raw_input: object | None = None) -> ToolKind: ...

    def approval_policy_key(self, tool_name: str, raw_input: object | None = None) -> str: ...


class DefaultToolClassifier:
    def classify(self, tool_name: str, raw_input: object | None = None) -> ToolKind:
        del raw_input
        lowered = tool_name.lower()
        if lowered.startswith(("read_", "load_", "open_", "cat_")):
            return "read"
        if lowered.startswith(("write_", "edit_", "patch_", "update_")):
            return "edit"
        if lowered.startswith(("delete_", "remove_")):
            return "delete"
        if lowered.startswith(("move_", "rename_")):
            return "move"
        if lowered.startswith(("search_", "grep_", "find_")):
            return "search"
        if lowered.startswith(("fetch_", "scrape_", "download_")):
            return "fetch"
        if lowered.startswith(("think_", "plan_")):
            return "think"
        return "execute"

    def approval_policy_key(self, tool_name: str, raw_input: object | None = None) -> str:
        del raw_input
        return tool_name


@dataclass(slots=True, kw_only=True)
class _ProjectedToolCall:
    kind: ToolKind
    locations: list[ToolCallLocation] | None
    title: str


def _is_output_tool(tool_name: str) -> bool:
    return tool_name == "final_result"


def extract_tool_call_locations(raw_input: object) -> list[ToolCallLocation] | None:
    if not _is_string_keyed_object_dict(raw_input):
        return None

    for key in _PATH_KEYS:
        value = raw_input.get(key)
        if isinstance(value, str) and value:
            return [ToolCallLocation(path=value)]
    return None


def build_tool_updates(
    messages: list[ModelMessage],
    *,
    classifier: ToolClassifier,
    serializer: OutputSerializer,
) -> list[ToolCallProgress | ToolCallStart]:
    projected_calls: dict[str, _ProjectedToolCall] = {}
    updates: list[ToolCallProgress | ToolCallStart] = []

    for message in messages:
        if isinstance(message, ModelResponse):
            for part in message.parts:
                if not isinstance(part, ToolCallPart) or _is_output_tool(part.tool_name):
                    continue
                raw_input = part.args_as_dict()
                projected_call = _ProjectedToolCall(
                    kind=classifier.classify(part.tool_name, raw_input),
                    locations=extract_tool_call_locations(raw_input),
                    title=part.tool_name,
                )
                projected_calls[part.tool_call_id] = projected_call
                updates.append(
                    ToolCallStart(
                        session_update="tool_call",
                        tool_call_id=part.tool_call_id,
                        title=projected_call.title,
                        kind=projected_call.kind,
                        status="in_progress",
                        locations=projected_call.locations,
                        raw_input=raw_input,
                    )
                )
        else:
            for part in message.parts:
                if isinstance(part, ToolReturnPart):
                    if _is_output_tool(part.tool_name):
                        continue
                    projected_call = projected_calls.get(part.tool_call_id)
                    updates.append(
                        ToolCallProgress(
                            session_update="tool_call_update",
                            tool_call_id=part.tool_call_id,
                            title=(
                                projected_call.title
                                if projected_call is not None
                                else part.tool_name
                            ),
                            kind=(
                                projected_call.kind
                                if projected_call is not None
                                else classifier.classify(part.tool_name)
                            ),
                            status=("completed" if part.outcome == "success" else "failed"),
                            locations=(
                                projected_call.locations if projected_call is not None else None
                            ),
                            raw_output=serializer.serialize(part.content),
                        )
                    )
                elif (
                    isinstance(part, RetryPromptPart)
                    and part.tool_name is not None
                    and not _is_output_tool(part.tool_name)
                ):
                    projected_call = projected_calls.get(part.tool_call_id)
                    updates.append(
                        ToolCallProgress(
                            session_update="tool_call_update",
                            tool_call_id=part.tool_call_id,
                            title=(
                                projected_call.title
                                if projected_call is not None
                                else part.tool_name
                            ),
                            kind=(
                                projected_call.kind
                                if projected_call is not None
                                else classifier.classify(part.tool_name)
                            ),
                            status="failed",
                            locations=(
                                projected_call.locations if projected_call is not None else None
                            ),
                            raw_output=part.model_response(),
                        )
                    )

    return updates
