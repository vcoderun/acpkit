from __future__ import annotations as _annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from acp.schema import (
    ContentToolCallContent,
    FileEditToolCallContent,
    TerminalToolCallContent,
    TextContentBlock,
    ToolCallLocation,
    ToolCallProgress,
    ToolCallStart,
    ToolCallStatus,
    ToolKind,
)
from pydantic_ai import ModelMessage, ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart
from typing_extensions import TypeIs

from .serialization import OutputSerializer

__all__ = (
    "CompositeProjectionMap",
    "DefaultToolClassifier",
    "FileSystemProjectionMap",
    "ProjectionMap",
    "ToolClassifier",
    "build_tool_progress_update",
    "build_tool_start_update",
    "build_tool_updates",
    "compose_projection_maps",
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
_CONTENT_KEYS = ("content", "text", "new_text")
_OLD_TEXT_KEYS = ("old_text", "oldText", "previous_content", "previous_text")
_COMMAND_KEYS = ("command", "cmd", "script", "bash")
_TERMINAL_ID_KEYS = ("terminal_id", "terminalId")
_MAX_COMMAND_PREVIEW_CHARS = 4000
_MAX_COMMAND_TITLE_CHARS = 80


def _is_string_keyed_object_dict(value: Any) -> TypeIs[dict[str, Any]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


class ToolClassifier(Protocol):
    def classify(self, tool_name: str, raw_input: Any = None) -> ToolKind: ...

    def approval_policy_key(self, tool_name: str, raw_input: Any = None) -> str: ...


@dataclass(slots=True, frozen=True, kw_only=True)
class ToolProjection:
    content: (
        list[ContentToolCallContent | FileEditToolCallContent | TerminalToolCallContent] | None
    ) = None
    locations: list[ToolCallLocation] | None = None
    status: ToolCallStatus | None = None
    title: str | None = None


class ProjectionMap(Protocol):
    def project_start(
        self,
        tool_name: str,
        *,
        cwd: Path | None = None,
        raw_input: Any = None,
    ) -> ToolProjection | None: ...

    def project_progress(
        self,
        tool_name: str,
        *,
        cwd: Path | None = None,
        raw_input: Any = None,
        raw_output: Any = None,
        serialized_output: str,
        status: ToolCallStatus,
    ) -> ToolProjection | None: ...


@dataclass(slots=True, frozen=True, kw_only=True)
class CompositeProjectionMap:
    maps: tuple[ProjectionMap, ...]

    def project_start(
        self,
        tool_name: str,
        *,
        cwd: Path | None = None,
        raw_input: Any = None,
    ) -> ToolProjection | None:
        return _merge_tool_projections(
            projection_map.project_start(tool_name, cwd=cwd, raw_input=raw_input)
            for projection_map in self.maps
        )

    def project_progress(
        self,
        tool_name: str,
        *,
        cwd: Path | None = None,
        raw_input: Any = None,
        raw_output: Any = None,
        serialized_output: str,
        status: ToolCallStatus,
    ) -> ToolProjection | None:
        return _merge_tool_projections(
            projection_map.project_progress(
                tool_name,
                cwd=cwd,
                raw_input=raw_input,
                raw_output=raw_output,
                serialized_output=serialized_output,
                status=status,
            )
            for projection_map in self.maps
        )


def compose_projection_maps(
    projection_maps: Sequence[ProjectionMap] | None,
) -> ProjectionMap | None:
    if projection_maps is None:
        return None
    if len(projection_maps) == 0:
        return None
    if len(projection_maps) == 1:
        return projection_maps[0]
    return CompositeProjectionMap(maps=tuple(projection_maps))


@dataclass(slots=True, frozen=True, kw_only=True)
class FileSystemProjectionMap:
    write_tool_names: frozenset[str] = frozenset()
    read_tool_names: frozenset[str] = frozenset()
    bash_tool_names: frozenset[str] = frozenset()
    default_write_tool: str | None = None
    default_read_tool: str | None = None
    default_bash_tool: str | None = None
    path_arg: str | None = None
    content_arg: str | None = None
    old_text_arg: str | None = None
    command_arg: str | None = None
    terminal_id_arg: str | None = None

    def project_start(
        self,
        tool_name: str,
        *,
        cwd: Path | None = None,
        raw_input: Any = None,
    ) -> ToolProjection | None:
        if tool_name in self._bash_tool_names():
            if not _is_string_keyed_object_dict(raw_input):
                return None
            command = self._command_from_input(raw_input)
            if command is None:
                return None
            return ToolProjection(
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=_text_block(_format_command_preview(command)),
                    )
                ],
                title=_format_command_title(command),
            )
        if tool_name not in self._write_tool_names():
            return None
        if not _is_string_keyed_object_dict(raw_input):
            return None
        path = self._path_from_input(raw_input)
        new_text = self._content_from_input(raw_input)
        if path is None or new_text is None:
            return None
        return ToolProjection(
            content=[
                FileEditToolCallContent(
                    type="diff",
                    path=path,
                    old_text=self._old_text_from_input(raw_input, path=path, cwd=cwd),
                    new_text=new_text,
                )
            ],
            locations=[ToolCallLocation(path=path)],
        )

    def project_progress(
        self,
        tool_name: str,
        *,
        cwd: Path | None = None,
        raw_input: Any = None,
        raw_output: Any = None,
        serialized_output: str,
        status: ToolCallStatus,
    ) -> ToolProjection | None:
        if tool_name in self._bash_tool_names():
            terminal_id = self._terminal_id_from_value(raw_output)
            status_override = _bash_status(raw_output, fallback=status)
            if terminal_id is not None:
                return ToolProjection(
                    content=[TerminalToolCallContent(type="terminal", terminal_id=terminal_id)],
                    status=status_override,
                )
            return ToolProjection(
                content=_bash_progress_content(
                    raw_input=raw_input,
                    raw_output=raw_output,
                    serialized_output=serialized_output,
                ),
                status=status_override,
            )
        if status != "completed":
            return None
        if tool_name in self._write_tool_names():
            return self.project_start(tool_name, cwd=cwd, raw_input=raw_input)
        if tool_name not in self._read_tool_names():
            return None
        if not _is_string_keyed_object_dict(raw_input):
            return None
        path = self._path_from_input(raw_input)
        if path is None:
            return None
        new_text = (
            serialized_output
            if raw_output is None
            else _stringify_value(raw_output, serialized_output)
        )
        return ToolProjection(
            content=[
                FileEditToolCallContent(
                    type="diff",
                    path=path,
                    old_text="",
                    new_text=new_text,
                )
            ],
            locations=[ToolCallLocation(path=path)],
        )

    def _write_tool_names(self) -> frozenset[str]:
        names = set(self.write_tool_names)
        if self.default_write_tool is not None:
            names.add(self.default_write_tool)
        return frozenset(names)

    def _read_tool_names(self) -> frozenset[str]:
        names = set(self.read_tool_names)
        if self.default_read_tool is not None:
            names.add(self.default_read_tool)
        return frozenset(names)

    def _bash_tool_names(self) -> frozenset[str]:
        names = set(self.bash_tool_names)
        if self.default_bash_tool is not None:
            names.add(self.default_bash_tool)
        return frozenset(names)

    def _path_from_input(self, raw_input: dict[str, Any]) -> str | None:
        for key in _candidate_keys(self.path_arg, _PATH_KEYS):
            value = raw_input.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _content_from_input(self, raw_input: dict[str, Any]) -> str | None:
        for key in _candidate_keys(self.content_arg, _CONTENT_KEYS):
            value = raw_input.get(key)
            if value is not None:
                return _stringify_value(value, None)
        return None

    def _command_from_input(self, raw_input: dict[str, Any]) -> str | None:
        for key in _candidate_keys(self.command_arg, _COMMAND_KEYS):
            value = raw_input.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _old_text_from_input(
        self,
        raw_input: dict[str, Any],
        *,
        path: str,
        cwd: Path | None,
    ) -> str:
        for key in _candidate_keys(self.old_text_arg, _OLD_TEXT_KEYS):
            value = raw_input.get(key)
            if value is not None:
                return _stringify_value(value, None)
        return _read_existing_text(path, cwd=cwd)

    def _terminal_id_from_value(self, value: Any) -> str | None:
        if not _is_string_keyed_object_dict(value):
            return None
        for key in _candidate_keys(self.terminal_id_arg, _TERMINAL_ID_KEYS):
            terminal_id = value.get(key)
            if isinstance(terminal_id, str) and terminal_id:
                return terminal_id
        return None


class DefaultToolClassifier:
    def classify(self, tool_name: str, raw_input: Any = None) -> ToolKind:
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

    def approval_policy_key(self, tool_name: str, raw_input: Any = None) -> str:
        del raw_input
        return tool_name


def _is_output_tool(tool_name: str) -> bool:
    return tool_name == "final_result"


def extract_tool_call_locations(raw_input: Any) -> list[ToolCallLocation] | None:
    if not _is_string_keyed_object_dict(raw_input):
        return None

    for key in _PATH_KEYS:
        value = raw_input.get(key)
        if isinstance(value, str) and value:
            return [ToolCallLocation(path=value)]
    return None


def _candidate_keys(explicit_key: str | None, fallback_keys: tuple[str, ...]) -> tuple[str, ...]:
    if explicit_key is None:
        return fallback_keys
    return (explicit_key, *fallback_keys)


def _merge_tool_projections(
    projections: Iterable[ToolProjection | None],
) -> ToolProjection | None:
    merged_content: (
        list[ContentToolCallContent | FileEditToolCallContent | TerminalToolCallContent] | None
    ) = None
    merged_locations: list[ToolCallLocation] | None = None
    merged_status: ToolCallStatus | None = None
    merged_title: str | None = None
    saw_projection = False
    for projection in projections:
        if not isinstance(projection, ToolProjection):
            continue
        saw_projection = True
        if projection.content is not None:
            merged_content = projection.content
        if projection.locations is not None:
            merged_locations = projection.locations
        if projection.status is not None:
            merged_status = projection.status
        if projection.title is not None:
            merged_title = projection.title
    if not saw_projection:
        return None
    return ToolProjection(
        content=merged_content,
        locations=merged_locations,
        status=merged_status,
        title=merged_title,
    )


def _preserve_file_diff_content(
    *,
    known_start: ToolCallStart | None,
    projection: ToolProjection | None,
) -> list[ContentToolCallContent | FileEditToolCallContent | TerminalToolCallContent] | None:
    if projection is None or projection.content is None:
        return None
    if known_start is None or known_start.content is None:
        return projection.content
    if len(projection.content) != len(known_start.content):
        return projection.content
    if not projection.content or not known_start.content:
        return projection.content
    if not all(isinstance(content, FileEditToolCallContent) for content in projection.content):
        return projection.content
    if not all(isinstance(content, FileEditToolCallContent) for content in known_start.content):
        return projection.content
    return known_start.content


def _stringify_value(value: Any, serialized_fallback: str | None) -> str:
    if isinstance(value, str):
        return value
    if serialized_fallback is not None:
        return serialized_fallback
    return str(value)


def _read_existing_text(path: str, *, cwd: Path | None) -> str:
    target_path = Path(path)
    if not target_path.is_absolute():
        if cwd is None:
            return ""
        target_path = cwd / target_path
    try:
        if not target_path.exists() or not target_path.is_file():
            return ""
        return target_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _preview_text(text: str) -> str:
    if len(text) <= _MAX_COMMAND_PREVIEW_CHARS:
        return text
    return f"{text[:_MAX_COMMAND_PREVIEW_CHARS]}\n\n...[truncated]"


def _single_line_preview(text: str, *, limit: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit].rstrip()}..."


def _format_command_title(command: str) -> str:
    return f"Execute {_single_line_preview(command, limit=_MAX_COMMAND_TITLE_CHARS)}"


def _format_command_preview(command: str) -> str:
    return f"```bash\n{_preview_text(command)}\n```"


def _bash_status(raw_output: Any = None, *, fallback: ToolCallStatus) -> ToolCallStatus:
    if not _is_string_keyed_object_dict(raw_output):
        return fallback
    timed_out = raw_output.get("timed_out")
    if isinstance(timed_out, bool) and timed_out:
        return "failed"
    if isinstance(timed_out, int) and timed_out != 0:
        return "failed"
    returncode = raw_output.get("returncode")
    if isinstance(returncode, int) and returncode != 0:
        return "failed"
    return fallback


def _bash_progress_content(
    *,
    raw_input: Any,
    raw_output: Any,
    serialized_output: str,
) -> list[ContentToolCallContent | FileEditToolCallContent | TerminalToolCallContent]:
    command = None
    if _is_string_keyed_object_dict(raw_input):
        for key in _COMMAND_KEYS:
            value = raw_input.get(key)
            if isinstance(value, str) and value:
                command = value
                break

    if not _is_string_keyed_object_dict(raw_output):
        return [ContentToolCallContent(type="content", content=_text_block(serialized_output))]

    status_label = (
        "failed" if _bash_status(raw_output, fallback="completed") == "failed" else "success"
    )
    sections: list[str] = [f"Status: {status_label}"]
    if command is not None:
        sections.extend(("", _format_command_preview(command)))
    returncode = raw_output.get("returncode")
    if isinstance(returncode, int):
        sections.append(f"Exit code: {returncode}")
    stdout = raw_output.get("stdout")
    if isinstance(stdout, str) and stdout:
        sections.extend(("", "Stdout:", f"```text\n{_preview_text(stdout)}\n```"))
    stderr = raw_output.get("stderr")
    if isinstance(stderr, str) and stderr:
        sections.extend(("", "Stderr:", f"```text\n{_preview_text(stderr)}\n```"))
    return [ContentToolCallContent(type="content", content=_text_block("\n".join(sections)))]


def _text_block(text: str) -> TextContentBlock:
    return TextContentBlock(type="text", text=text)


def build_tool_start_update(
    part: ToolCallPart,
    *,
    classifier: ToolClassifier,
    cwd: Path | None = None,
    projection_map: ProjectionMap | None,
) -> ToolCallStart:
    raw_input = part.args_as_dict()
    kind = classifier.classify(part.tool_name, raw_input)
    projection = None
    if projection_map is not None:
        projection = projection_map.project_start(
            part.tool_name,
            cwd=cwd,
            raw_input=raw_input,
        )
    return ToolCallStart(
        session_update="tool_call",
        tool_call_id=part.tool_call_id,
        title=(
            projection.title
            if projection is not None and projection.title is not None
            else part.tool_name
        ),
        kind=kind,
        status="in_progress",
        content=projection.content if projection is not None else None,
        locations=(
            projection.locations
            if projection is not None and projection.locations is not None
            else extract_tool_call_locations(raw_input)
        ),
        raw_input=raw_input,
    )


def build_tool_progress_update(
    part: ToolReturnPart | RetryPromptPart,
    *,
    classifier: ToolClassifier,
    cwd: Path | None = None,
    known_start: ToolCallStart | None,
    projection_map: ProjectionMap | None,
    serializer: OutputSerializer,
) -> ToolCallProgress:
    known_start_raw_input = (
        known_start.raw_input
        if known_start is not None and _is_string_keyed_object_dict(known_start.raw_input)
        else None
    )
    projection = None
    serialized_output: str | None = None
    status: ToolCallStatus | None = None
    if isinstance(part, ToolReturnPart):
        serialized_output = serializer.serialize(part.content)
        status = "completed" if part.outcome == "success" else "failed"
        if projection_map is not None:
            projection = projection_map.project_progress(
                part.tool_name,
                cwd=cwd,
                raw_input=known_start_raw_input,
                raw_output=part.content,
                serialized_output=serialized_output,
                status=status,
            )
    projected_content = _preserve_file_diff_content(
        known_start=known_start,
        projection=projection,
    )
    title = (
        projection.title
        if projection is not None and projection.title is not None
        else (
            known_start.title
            if known_start is not None
            else (part.tool_name if part.tool_name is not None else "")
        )
    )
    tool_name = part.tool_name if part.tool_name is not None else title
    kind: ToolKind = (
        known_start.kind
        if known_start is not None and known_start.kind is not None
        else classifier.classify(tool_name)
    )
    locations = known_start.locations if known_start is not None else None
    if isinstance(part, ToolReturnPart):
        return ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id=part.tool_call_id,
            title=title,
            kind=kind,
            status=(
                projection.status
                if projection is not None and projection.status is not None
                else status
            ),
            content=(
                projected_content
                if projected_content is not None
                else (known_start.content if known_start is not None else None)
            ),
            locations=(
                projection.locations
                if projection is not None and projection.locations is not None
                else (
                    locations
                    if locations is not None
                    else (
                        extract_tool_call_locations(known_start_raw_input)
                        if known_start_raw_input is not None
                        else None
                    )
                )
            ),
            raw_output=serialized_output,
        )

    return ToolCallProgress(
        session_update="tool_call_update",
        tool_call_id=part.tool_call_id,
        title=title,
        kind=kind,
        status="failed",
        locations=locations,
        raw_output=part.model_response(),
    )


def build_tool_updates(
    messages: list[ModelMessage],
    *,
    classifier: ToolClassifier,
    cwd: Path | None = None,
    known_starts: dict[str, ToolCallStart] | None = None,
    projection_map: ProjectionMap | None,
    serializer: OutputSerializer,
) -> list[ToolCallProgress | ToolCallStart]:
    known_call_starts = dict(known_starts or {})
    updates: list[ToolCallProgress | ToolCallStart] = []

    for message in messages:
        if isinstance(message, ModelResponse):
            for part in message.parts:
                if not isinstance(part, ToolCallPart) or _is_output_tool(part.tool_name):
                    continue
                if part.tool_call_id in known_call_starts:
                    continue
                start_update = build_tool_start_update(
                    part,
                    classifier=classifier,
                    cwd=cwd,
                    projection_map=projection_map,
                )
                known_call_starts[part.tool_call_id] = start_update
                updates.append(start_update)
        else:
            for part in message.parts:
                if isinstance(part, ToolReturnPart):
                    if _is_output_tool(part.tool_name):
                        continue
                    updates.append(
                        build_tool_progress_update(
                            part,
                            classifier=classifier,
                            cwd=cwd,
                            known_start=known_call_starts.get(part.tool_call_id),
                            projection_map=projection_map,
                            serializer=serializer,
                        )
                    )
                elif (
                    isinstance(part, RetryPromptPart)
                    and part.tool_name is not None
                    and not _is_output_tool(part.tool_name)
                ):
                    updates.append(
                        build_tool_progress_update(
                            part,
                            classifier=classifier,
                            cwd=cwd,
                            known_start=known_call_starts.get(part.tool_call_id),
                            projection_map=projection_map,
                            serializer=serializer,
                        )
                    )

    return updates
