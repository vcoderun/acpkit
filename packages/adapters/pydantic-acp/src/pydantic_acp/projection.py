from __future__ import annotations as _annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
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
from pydantic_ai import (
    BuiltinToolCallPart,
    BuiltinToolReturnPart,
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.messages import CompactionPart
from typing_extensions import TypeIs

from ._projection_text import (
    format_code_block,
    single_line_summary,
    truncate_text,
)
from .serialization import OutputSerializer
from .session.state import JsonValue

__all__ = (
    "BuiltinToolProjectionMap",
    "CompositeProjectionMap",
    "DefaultToolClassifier",
    "FileSystemProjectionMap",
    "ProjectionMap",
    "ToolClassifier",
    "WebToolProjectionMap",
    "build_tool_progress_update",
    "build_tool_start_update",
    "build_compaction_updates",
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
_MAX_WEB_PREVIEW_CHARS = 2000
_DEFAULT_SEARCH_TOOL_NAMES = frozenset(
    {"duckduckgo_search", "exa_search", "tavily_search", "web_search"}
)
_DEFAULT_FETCH_TOOL_NAMES = frozenset({"web_fetch"})
_DEFAULT_IMAGE_GENERATION_TOOL_NAMES = frozenset({"generate_image", "image_generation"})
_DEFAULT_MCP_TOOL_NAME_PREFIXES = frozenset({"mcp_server:"})


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


@dataclass(slots=True, frozen=True, kw_only=True)
class WebToolProjectionMap:
    search_tool_names: frozenset[str] = _DEFAULT_SEARCH_TOOL_NAMES
    fetch_tool_names: frozenset[str] = _DEFAULT_FETCH_TOOL_NAMES

    def project_start(
        self,
        tool_name: str,
        *,
        cwd: Path | None = None,
        raw_input: Any = None,
    ) -> ToolProjection | None:
        del cwd
        if not _is_string_keyed_object_dict(raw_input):
            return None
        if tool_name in self.search_tool_names:
            query = _web_search_query(raw_input)
            if query is None:
                return None
            return ToolProjection(
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=_text_block(_format_web_search_start(raw_input)),
                    )
                ],
                title=f"Search web for {_single_line_preview(query, limit=_MAX_COMMAND_TITLE_CHARS)}",
            )
        if tool_name not in self.fetch_tool_names:
            return None
        url = _web_fetch_url(raw_input)
        if url is None:
            return None
        return ToolProjection(
            content=[
                ContentToolCallContent(
                    type="content",
                    content=_text_block(_format_web_fetch_start(raw_input)),
                )
            ],
            title=f"Fetch {_single_line_preview(url, limit=_MAX_COMMAND_TITLE_CHARS)}",
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
        del cwd, raw_input
        if status != "completed":
            return None
        if tool_name in self.search_tool_names:
            return ToolProjection(
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=_text_block(
                            _format_web_search_progress(raw_output, serialized_output)
                        ),
                    )
                ]
            )
        if tool_name not in self.fetch_tool_names:
            return None
        return ToolProjection(
            content=[
                ContentToolCallContent(
                    type="content",
                    content=_text_block(_format_web_fetch_progress(raw_output, serialized_output)),
                )
            ]
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class BuiltinToolProjectionMap:
    web_projection_map: WebToolProjectionMap = field(default_factory=WebToolProjectionMap)
    image_generation_tool_names: frozenset[str] = _DEFAULT_IMAGE_GENERATION_TOOL_NAMES
    mcp_tool_name_prefixes: frozenset[str] = _DEFAULT_MCP_TOOL_NAME_PREFIXES

    def project_start(
        self,
        tool_name: str,
        *,
        cwd: Path | None = None,
        raw_input: Any = None,
    ) -> ToolProjection | None:
        web_projection = self.web_projection_map.project_start(
            tool_name,
            cwd=cwd,
            raw_input=raw_input,
        )
        if web_projection is not None:
            return web_projection
        if not _is_string_keyed_object_dict(raw_input):
            return None
        if tool_name in self.image_generation_tool_names:
            prompt = _first_string(raw_input, "prompt", "input", "text")
            return ToolProjection(
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=_text_block(_format_image_generation_start(raw_input)),
                    )
                ],
                title=(
                    f"Generate image for {_single_line_preview(prompt, limit=_MAX_COMMAND_TITLE_CHARS)}"
                    if prompt is not None
                    else "Generate image"
                ),
            )
        if not any(tool_name.startswith(prefix) for prefix in self.mcp_tool_name_prefixes):
            return None
        return ToolProjection(
            content=[
                ContentToolCallContent(
                    type="content",
                    content=_text_block(_format_mcp_start(tool_name, raw_input)),
                )
            ],
            title=_format_mcp_title(tool_name, raw_input),
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
        web_projection = self.web_projection_map.project_progress(
            tool_name,
            cwd=cwd,
            raw_input=raw_input,
            raw_output=raw_output,
            serialized_output=serialized_output,
            status=status,
        )
        if web_projection is not None:
            return web_projection
        if status != "completed":
            return None
        if tool_name in self.image_generation_tool_names:
            return ToolProjection(
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=_text_block(
                            _format_image_generation_progress(raw_output, serialized_output)
                        ),
                    )
                ]
            )
        if not any(tool_name.startswith(prefix) for prefix in self.mcp_tool_name_prefixes):
            return None
        return ToolProjection(
            content=[
                ContentToolCallContent(
                    type="content",
                    content=_text_block(_format_mcp_progress(raw_output, serialized_output)),
                )
            ]
        )


class DefaultToolClassifier:
    def classify(self, tool_name: str, raw_input: Any = None) -> ToolKind:
        del raw_input
        lowered = tool_name.lower()
        if lowered in _DEFAULT_SEARCH_TOOL_NAMES:
            return "search"
        if lowered in _DEFAULT_FETCH_TOOL_NAMES:
            return "fetch"
        if lowered in _DEFAULT_IMAGE_GENERATION_TOOL_NAMES:
            return "execute"
        if any(lowered.startswith(prefix) for prefix in _DEFAULT_MCP_TOOL_NAME_PREFIXES):
            return "execute"
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
    return truncate_text(text, limit=_MAX_COMMAND_PREVIEW_CHARS)


def _single_line_preview(text: str, *, limit: int) -> str:
    return single_line_summary(text, limit=limit)


def _format_command_title(command: str) -> str:
    return f"Execute {_single_line_preview(command, limit=_MAX_COMMAND_TITLE_CHARS)}"


def _format_command_preview(command: str) -> str:
    return format_code_block(
        command,
        language="bash",
        limit=_MAX_COMMAND_PREVIEW_CHARS,
    )


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
        sections.extend(
            (
                "",
                "Stdout:",
                format_code_block(stdout, language="text", limit=_MAX_COMMAND_PREVIEW_CHARS),
            )
        )
    stderr = raw_output.get("stderr")
    if isinstance(stderr, str) and stderr:
        sections.extend(
            (
                "",
                "Stderr:",
                format_code_block(stderr, language="text", limit=_MAX_COMMAND_PREVIEW_CHARS),
            )
        )
    return [ContentToolCallContent(type="content", content=_text_block("\n".join(sections)))]


def _web_search_query(raw_input: dict[str, Any]) -> str | None:
    for key in ("query", "q", "search_query"):
        value = raw_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _web_fetch_url(raw_input: dict[str, Any]) -> str | None:
    for key in ("url", "href", "uri"):
        value = raw_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _format_web_search_start(raw_input: dict[str, Any]) -> str:
    lines: list[str] = []
    query = _web_search_query(raw_input)
    if query is not None:
        lines.append(f"Query: {query}")
    _append_string_list_line(lines, "Allowed domains", raw_input.get("allowed_domains"))
    _append_string_list_line(lines, "Blocked domains", raw_input.get("blocked_domains"))
    context_size = raw_input.get("search_context_size")
    if isinstance(context_size, str) and context_size:
        lines.append(f"Context size: {context_size}")
    user_location = raw_input.get("user_location")
    if _is_string_keyed_object_dict(user_location):
        location_parts = [
            str(user_location[key])
            for key in ("city", "region", "country", "timezone")
            if key in user_location and user_location[key] not in (None, "")
        ]
        if location_parts:
            lines.append(f"User location: {', '.join(location_parts)}")
    return "\n".join(lines) if lines else "Searching the web."


def _format_web_fetch_start(raw_input: dict[str, Any]) -> str:
    lines: list[str] = []
    url = _web_fetch_url(raw_input)
    if url is not None:
        lines.append(f"URL: {url}")
    _append_string_list_line(lines, "Allowed domains", raw_input.get("allowed_domains"))
    _append_string_list_line(lines, "Blocked domains", raw_input.get("blocked_domains"))
    max_content_tokens = raw_input.get("max_content_tokens")
    if isinstance(max_content_tokens, int):
        lines.append(f"Max content tokens: {max_content_tokens}")
    enable_citations = raw_input.get("enable_citations")
    if isinstance(enable_citations, bool):
        lines.append(f"Citations enabled: {'yes' if enable_citations else 'no'}")
    return "\n".join(lines) if lines else "Fetching web content."


def _append_string_list_line(lines: list[str], label: str, value: Any) -> None:
    if not isinstance(value, list) or not value:
        return
    entries = [entry for entry in value if isinstance(entry, str) and entry]
    if entries:
        lines.append(f"{label}: {', '.join(entries)}")


def _format_web_search_progress(raw_output: Any, serialized_output: str) -> str:
    results = _extract_search_results(raw_output)
    if not results:
        return truncate_text(serialized_output, limit=_MAX_WEB_PREVIEW_CHARS)
    sections: list[str] = []
    for index, result in enumerate(results[:5], start=1):
        title = _first_string(result, "title", "name") or f"Result {index}"
        url = _first_string(result, "url", "href", "link")
        snippet = _first_string(result, "body", "snippet", "description", "content")
        sections.append(f"{index}. {title}")
        if url is not None:
            sections.append(url)
        if snippet is not None:
            sections.append(_single_line_preview(snippet, limit=220))
        sections.append("")
    return "\n".join(sections).rstrip()


def _extract_search_results(raw_output: Any) -> list[dict[str, Any]] | None:
    if isinstance(raw_output, list) and all(
        _is_string_keyed_object_dict(item) for item in raw_output
    ):
        results: list[dict[str, Any]] = []
        results.extend(item for item in raw_output if _is_string_keyed_object_dict(item))
        return results
    if not _is_string_keyed_object_dict(raw_output):
        return None
    for key in ("results", "items", "data"):
        value = raw_output.get(key)
        if isinstance(value, list) and all(_is_string_keyed_object_dict(item) for item in value):
            nested_results: list[dict[str, Any]] = []
            nested_results.extend(item for item in value if _is_string_keyed_object_dict(item))
            return nested_results
    return None


def _format_web_fetch_progress(raw_output: Any, serialized_output: str) -> str:
    if _is_binary_like_content(raw_output):
        media_type = getattr(raw_output, "media_type", None)
        if isinstance(media_type, str) and media_type:
            return f"Fetched binary content ({media_type})."
        return "Fetched binary content."
    if not _is_string_keyed_object_dict(raw_output):
        return truncate_text(serialized_output, limit=_MAX_WEB_PREVIEW_CHARS)

    lines: list[str] = []
    url = _first_string(raw_output, "url", "href")
    if url is not None:
        lines.append(f"URL: {url}")
    title = _first_string(raw_output, "title", "name")
    if title is not None:
        lines.append(f"Title: {title}")
    content = _first_string(raw_output, "content", "body", "text")
    if content is not None:
        lines.extend(
            (
                "",
                "Preview:",
                format_code_block(content, language="text", limit=_MAX_WEB_PREVIEW_CHARS),
            )
        )
    if not lines:
        return truncate_text(serialized_output, limit=_MAX_WEB_PREVIEW_CHARS)
    return "\n".join(lines)


def _format_image_generation_start(raw_input: dict[str, Any]) -> str:
    lines: list[str] = []
    prompt = _first_string(raw_input, "prompt", "input", "text")
    if prompt is not None:
        lines.append(f"Prompt: {prompt}")
    for label, key in (
        ("Quality", "quality"),
        ("Size", "size"),
        ("Aspect ratio", "aspect_ratio"),
        ("Output format", "output_format"),
        ("Background", "background"),
    ):
        value = raw_input.get(key)
        if isinstance(value, str) and value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines) if lines else "Generating image."


def _format_image_generation_progress(raw_output: Any, serialized_output: str) -> str:
    if not _is_string_keyed_object_dict(raw_output):
        return truncate_text(serialized_output, limit=_MAX_WEB_PREVIEW_CHARS)
    lines: list[str] = []
    status = _first_string(raw_output, "status")
    if status is not None:
        lines.append(f"Status: {status}")
    revised_prompt = _first_string(raw_output, "revised_prompt")
    if revised_prompt is not None:
        lines.append(f"Revised prompt: {revised_prompt}")
    for label, key in (
        ("Quality", "quality"),
        ("Size", "size"),
        ("Background", "background"),
    ):
        value = raw_output.get(key)
        if isinstance(value, str) and value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines) if lines else "Image generation completed."


def _format_mcp_title(tool_name: str, raw_input: dict[str, Any]) -> str:
    server_label = tool_name.split(":", 1)[1] if ":" in tool_name else tool_name
    action = _first_string(raw_input, "action")
    actual_tool_name = _first_string(raw_input, "tool_name")
    if action == "call_tool" and actual_tool_name is not None:
        return f"Call {actual_tool_name} via MCP {server_label}"
    if action == "list_tools":
        return f"List tools from MCP {server_label}"
    return f"Use MCP {server_label}"


def _format_mcp_start(tool_name: str, raw_input: dict[str, Any]) -> str:
    server_label = tool_name.split(":", 1)[1] if ":" in tool_name else tool_name
    lines = [f"Server: {server_label}"]
    action = _first_string(raw_input, "action")
    if action is not None:
        lines.append(f"Action: {action}")
    actual_tool_name = _first_string(raw_input, "tool_name")
    if actual_tool_name is not None:
        lines.append(f"Tool: {actual_tool_name}")
    tool_args = raw_input.get("tool_args")
    if _is_string_keyed_object_dict(tool_args) and tool_args:
        lines.extend(
            (
                "",
                "Arguments:",
                format_code_block(
                    _json_preview(tool_args),
                    language="json",
                    limit=_MAX_WEB_PREVIEW_CHARS,
                ),
            )
        )
    return "\n".join(lines)


def _format_mcp_progress(raw_output: Any, serialized_output: str) -> str:
    if not _is_string_keyed_object_dict(raw_output):
        return truncate_text(serialized_output, limit=_MAX_WEB_PREVIEW_CHARS)
    lines: list[str] = []
    error = raw_output.get("error")
    if error not in (None, ""):
        lines.append(f"Error: {error}")
    tools = raw_output.get("tools")
    if isinstance(tools, list):
        lines.append(f"Tools listed: {len(tools)}")
        preview_names: list[str] = []
        for item in tools[:5]:
            if not _is_string_keyed_object_dict(item):
                continue
            name = item.get("name")
            if isinstance(name, str):
                preview_names.append(name)
        if preview_names:
            lines.append(f"Preview: {', '.join(preview_names)}")
    output = raw_output.get("output")
    if output not in (None, "", []):
        lines.extend(
            (
                "",
                "Output:",
                format_code_block(
                    _json_preview(output),
                    language="json",
                    limit=_MAX_WEB_PREVIEW_CHARS,
                ),
            )
        )
    if not lines:
        return truncate_text(serialized_output, limit=_MAX_WEB_PREVIEW_CHARS)
    return "\n".join(lines)


def _first_string(value: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item:
            return item
    return None


def _json_preview(value: Any) -> str:
    try:
        return json.dumps(value, indent=2, sort_keys=True)
    except TypeError:
        return str(value)


def _is_binary_like_content(value: Any) -> bool:
    if value is None:
        return False
    media_type = getattr(value, "media_type", None)
    data = getattr(value, "data", None)
    return isinstance(media_type, str) and isinstance(data, bytes)


def _text_block(text: str) -> TextContentBlock:
    return TextContentBlock(type="text", text=text)


def _is_tool_call_part(value: Any) -> TypeIs[ToolCallPart | BuiltinToolCallPart]:
    return isinstance(value, (ToolCallPart, BuiltinToolCallPart))


def _is_tool_return_part(value: Any) -> TypeIs[ToolReturnPart | BuiltinToolReturnPart]:
    return isinstance(value, (ToolReturnPart, BuiltinToolReturnPart))


def _build_tool_start_projection(
    part: ToolCallPart | BuiltinToolCallPart,
    *,
    cwd: Path | None,
    projection_map: ProjectionMap | None,
) -> ToolProjection | None:
    if projection_map is None:
        return None
    return projection_map.project_start(
        part.tool_name,
        cwd=cwd,
        raw_input=part.args_as_dict(),
    )


def _build_tool_title(
    *,
    part_tool_name: str | None,
    known_start: ToolCallStart | None,
    projection: ToolProjection | None,
) -> str:
    if projection is not None and projection.title is not None:
        return projection.title
    if known_start is not None:
        return known_start.title
    return part_tool_name or ""


def _build_tool_locations(
    *,
    known_start: ToolCallStart | None,
    known_start_raw_input: dict[str, Any] | None,
    projection: ToolProjection | None,
) -> list[ToolCallLocation] | None:
    if projection is not None and projection.locations is not None:
        return projection.locations
    if known_start is not None and known_start.locations is not None:
        return known_start.locations
    if known_start_raw_input is None:
        return None
    return extract_tool_call_locations(known_start_raw_input)


def build_tool_start_update(
    part: ToolCallPart | BuiltinToolCallPart,
    *,
    classifier: ToolClassifier,
    cwd: Path | None = None,
    projection_map: ProjectionMap | None,
) -> ToolCallStart:
    raw_input = part.args_as_dict()
    projection = _build_tool_start_projection(
        part,
        cwd=cwd,
        projection_map=projection_map,
    )
    return ToolCallStart(
        session_update="tool_call",
        tool_call_id=part.tool_call_id,
        title=(
            projection.title
            if projection is not None and projection.title is not None
            else part.tool_name
        ),
        kind=classifier.classify(part.tool_name, raw_input),
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
    part: ToolReturnPart | BuiltinToolReturnPart | RetryPromptPart,
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
    if _is_tool_return_part(part):
        serialized_output = serializer.serialize(part.content)
        status = "completed"
        if isinstance(part, ToolReturnPart):
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
    title = _build_tool_title(
        part_tool_name=part.tool_name,
        known_start=known_start,
        projection=projection,
    )
    tool_name = part.tool_name if part.tool_name is not None else title
    kind: ToolKind = (
        known_start.kind
        if known_start is not None and known_start.kind is not None
        else classifier.classify(tool_name)
    )
    preserved_content = _preserve_file_diff_content(
        known_start=known_start,
        projection=projection,
    )
    if _is_tool_return_part(part):
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
                preserved_content
                if preserved_content is not None
                else (known_start.content if known_start is not None else None)
            ),
            locations=_build_tool_locations(
                known_start=known_start,
                known_start_raw_input=known_start_raw_input,
                projection=projection,
            ),
            raw_output=serialized_output,
        )

    return ToolCallProgress(
        session_update="tool_call_update",
        tool_call_id=part.tool_call_id,
        title=title,
        kind=kind,
        status="failed",
        locations=known_start.locations if known_start is not None else None,
        raw_output=part.model_response(),
    )


def _build_progress_updates_for_message(
    message: ModelMessage,
    *,
    classifier: ToolClassifier,
    cwd: Path | None,
    known_call_starts: dict[str, ToolCallStart],
    projection_map: ProjectionMap | None,
    serializer: OutputSerializer,
) -> list[ToolCallProgress | ToolCallStart]:
    updates: list[ToolCallProgress | ToolCallStart] = []
    if isinstance(message, ModelResponse):
        for part in message.parts:
            if _is_tool_call_part(part):
                if _is_output_tool(part.tool_name):
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
                continue
            if isinstance(part, BuiltinToolReturnPart):
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
        return updates

    for part in message.parts:
        if _is_tool_return_part(part):
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
        updates.extend(
            _build_progress_updates_for_message(
                message,
                classifier=classifier,
                cwd=cwd,
                known_call_starts=known_call_starts,
                projection_map=projection_map,
                serializer=serializer,
            )
        )
    return updates


def build_compaction_updates(
    messages: list[ModelMessage],
    *,
    known_starts: dict[str, ToolCallStart] | None = None,
    skip_providers: frozenset[str] = frozenset(),
) -> list[ToolCallProgress | ToolCallStart]:
    known_call_starts = dict(known_starts or {})
    updates: list[ToolCallProgress | ToolCallStart] = []
    created_count = 0
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        for part in message.parts:
            if not isinstance(part, CompactionPart):
                continue
            provider_name = (part.provider_name or "unknown").lower()
            if provider_name in skip_providers:
                continue
            tool_call_id = _compaction_tool_call_id(
                part,
                provider_name=provider_name,
                known_starts=known_call_starts,
                created_count=created_count,
            )
            created_count += 1
            known_start = known_call_starts.get(tool_call_id)
            if known_start is None:
                known_start = ToolCallStart(
                    session_update="tool_call",
                    tool_call_id=tool_call_id,
                    title="Context Compaction",
                    kind="execute",
                    status="in_progress",
                    raw_input=_compaction_raw_input(part, provider_name=provider_name),
                )
                known_call_starts[tool_call_id] = known_start
                updates.append(known_start)
            updates.append(
                ToolCallProgress(
                    session_update="tool_call_update",
                    tool_call_id=tool_call_id,
                    title=known_start.title,
                    kind=known_start.kind,
                    status="completed",
                    raw_output=_format_compaction_progress(part, provider_name=provider_name),
                )
            )
    return updates


def _compaction_tool_call_id(
    part: CompactionPart,
    *,
    provider_name: str,
    known_starts: dict[str, ToolCallStart],
    created_count: int,
) -> str:
    if part.id:
        return f"compaction:{provider_name}:{part.id}"
    candidate = f"compaction:{provider_name}:{len(known_starts) + created_count + 1}"
    while candidate in known_starts:
        created_count += 1
        candidate = f"compaction:{provider_name}:{len(known_starts) + created_count + 1}"
    return candidate


def _compaction_raw_input(
    part: CompactionPart,
    *,
    provider_name: str,
) -> dict[str, JsonValue]:
    raw_input: dict[str, JsonValue] = {"provider": provider_name}
    if part.id:
        raw_input["compaction_id"] = part.id
    return raw_input


def _format_compaction_progress(
    part: CompactionPart,
    *,
    provider_name: str,
) -> str:
    lines = [
        f"Provider: {provider_name}",
        "Status: history compacted",
    ]
    if part.content:
        lines.extend(("", "Summary:", truncate_text(part.content, limit=_MAX_WEB_PREVIEW_CHARS)))
    elif part.provider_details:
        lines.append("Compaction payload stored for round-trip.")
    else:
        lines.append("Compaction completed.")
    if part.id:
        lines.append(f"Compaction id: {part.id}")
    return "\n".join(lines)
