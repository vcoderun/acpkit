from __future__ import annotations as _annotations

import ast
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

__all__ = (
    "BrowserProjectionMap",
    "CommunityFileManagementProjectionMap",
    "CompositeProjectionMap",
    "CommandProjectionMap",
    "DeepAgentsProjectionMap",
    "DefaultToolClassifier",
    "FileSystemProjectionMap",
    "FinanceProjectionMap",
    "HttpRequestProjectionMap",
    "ProjectionMap",
    "ToolClassifier",
    "WebFetchProjectionMap",
    "WebSearchProjectionMap",
    "build_tool_progress_update",
    "build_tool_start_update",
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
_OLD_CONTENT_KEYS = ("old_string", "old_text")
_NEW_CONTENT_KEYS = ("content", "new_string", "new_text", "text")
_COMMAND_KEYS = ("command", "cmd", "script", "bash")
_SEARCH_KEYS = ("pattern", "query", "directory", "path")
_OUTPUT_TEXT_KEYS = ("output", "result", "stderr", "stdout", "text")
_TERMINAL_ID_KEYS = ("terminalId", "terminal_id")
_MAX_COMMAND_TITLE_CHARS = 80
_RISKY_COMMAND_PATTERNS = (
    " rm ",
    "chmod ",
    "curl |",
    "dd ",
    " git reset --hard",
    " mkfs",
    " sudo ",
)
_DEFAULT_WEB_SEARCH_TOOL_NAMES = frozenset(
    {
        "brave_search",
        "duckduckgo_results_json",
        "duckduckgo_search",
        "google_search",
        "google_search_results_json",
        "google_serper",
        "google_serper_results_json",
        "jina_search",
        "searchapi",
        "searchapi_results_json",
        "searx_search",
        "searx_search_results",
        "tavily_answer",
        "tavily_search_results_json",
    }
)
_DEFAULT_WEB_FETCH_TOOL_NAMES = frozenset(
    {
        "requests_delete",
        "requests_get",
        "requests_patch",
        "requests_post",
        "requests_put",
    }
)
_DEFAULT_BROWSER_NAVIGATE_TOOL_NAMES = frozenset({"navigate_browser"})
_DEFAULT_BROWSER_READ_TOOL_NAMES = frozenset(
    {"current_webpage", "extract_hyperlinks", "extract_text", "get_elements"}
)
_DEFAULT_BROWSER_ACTION_TOOL_NAMES = frozenset({"click_element", "previous_webpage"})
_DEFAULT_COMMAND_TOOL_NAMES = frozenset({"terminal"})
_DEFAULT_FILE_MANAGEMENT_READ_TOOL_NAMES = frozenset({"read_file"})
_DEFAULT_FILE_MANAGEMENT_WRITE_TOOL_NAMES = frozenset({"write_file"})
_DEFAULT_FILE_MANAGEMENT_SEARCH_TOOL_NAMES = frozenset({"file_search", "list_directory"})
_DEFAULT_FILE_MANAGEMENT_MUTATION_TOOL_NAMES = frozenset({"copy_file", "file_delete", "move_file"})
_DEFAULT_FINANCE_SEARCH_TOOL_NAMES = frozenset({"google_finance", "yahoo_finance_news"})
_DEFAULT_FINANCE_DATASET_TOOL_NAMES = frozenset(
    {"balance_sheets", "cash_flow_statements", "income_statements"}
)
_WEB_SEARCH_QUERY_KEYS = ("query", "q", "search_term", "search_query")
_WEB_FETCH_URL_KEYS = ("url", "href", "link", "uri")
_DIRECTORY_PATH_KEYS = ("dir_path",)
_SEARCH_RESULT_CONTAINER_KEYS = (
    "data",
    "items",
    "organic",
    "organic_results",
    "results",
    "value",
)
_SEARCH_RESULT_TITLE_KEYS = ("title", "name")
_SEARCH_RESULT_URL_KEYS = ("url", "href", "link")
_SEARCH_RESULT_SNIPPET_KEYS = ("snippet", "content", "body", "description", "text")
_FETCH_RESULT_TITLE_KEYS = ("title", "name")
_FETCH_RESULT_CONTENT_KEYS = ("content", "body", "text", "markdown")
_FINANCE_QUERY_KEYS = ("query", "ticker", "symbol")
_FINANCE_PERIOD_KEYS = ("period",)
_MAX_CONTENT_PREVIEW_CHARS = 480
_COMMAND_LIST_KEYS = ("commands",)


def _is_string_keyed_object_dict(value: Any) -> bool:
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
        serialized_output: str = "",
        status: ToolCallStatus = "completed",
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
class DefaultToolClassifier:
    def classify(self, tool_name: str, raw_input: Any = None) -> ToolKind:
        del raw_input
        normalized = tool_name.lower()
        if normalized in _DEFAULT_WEB_SEARCH_TOOL_NAMES:
            return "search"
        if normalized in _DEFAULT_WEB_FETCH_TOOL_NAMES:
            return "fetch"
        if normalized in _DEFAULT_BROWSER_NAVIGATE_TOOL_NAMES:
            return "fetch"
        if normalized in _DEFAULT_BROWSER_READ_TOOL_NAMES:
            return "read"
        if normalized in _DEFAULT_COMMAND_TOOL_NAMES:
            return "execute"
        if normalized in _DEFAULT_FILE_MANAGEMENT_READ_TOOL_NAMES:
            return "read"
        if normalized in _DEFAULT_FILE_MANAGEMENT_WRITE_TOOL_NAMES:
            return "edit"
        if normalized in _DEFAULT_FILE_MANAGEMENT_SEARCH_TOOL_NAMES:
            return "search"
        if normalized in _DEFAULT_FILE_MANAGEMENT_MUTATION_TOOL_NAMES:
            return "edit"
        if normalized in _DEFAULT_FINANCE_SEARCH_TOOL_NAMES:
            return "search"
        if normalized in _DEFAULT_FINANCE_DATASET_TOOL_NAMES:
            return "read"
        if normalized.startswith(("fetch_", "download_", "scrape_")):
            return "fetch"
        if normalized in {"glob", "grep", "ls"}:
            return "search"
        if "read" in normalized or normalized.startswith("list_"):
            return "read"
        if any(token in normalized for token in ("write", "edit", "patch", "update", "save")):
            return "edit"
        if any(token in normalized for token in ("bash", "command", "execute", "run", "shell")):
            return "execute"
        if "search" in normalized:
            return "search"
        return "other"

    def approval_policy_key(self, tool_name: str, raw_input: Any = None) -> str:
        del raw_input
        return tool_name


@dataclass(slots=True, frozen=True, kw_only=True)
class FileSystemProjectionMap:
    write_tool_names: frozenset[str] = frozenset()
    read_tool_names: frozenset[str] = frozenset()
    search_tool_names: frozenset[str] = frozenset()
    execute_tool_names: frozenset[str] = frozenset()

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
        if tool_name in self.execute_tool_names:
            command = _command_text(raw_input)
            if command is None:
                return None
            content: list[
                ContentToolCallContent | FileEditToolCallContent | TerminalToolCallContent
            ] = [ContentToolCallContent(type="content", content=_text_block(command))]
            risk_note = _command_risk_note(command)
            if risk_note is not None:
                content.append(
                    ContentToolCallContent(type="content", content=_text_block(risk_note))
                )
            return ToolProjection(
                content=content,
                title=_format_command_title(command),
            )
        if tool_name in self.write_tool_names:
            path = _first_string(raw_input, _PATH_KEYS)
            new_text = _first_string(raw_input, _NEW_CONTENT_KEYS)
            if path is None or new_text is None:
                return None
            old_text = _first_string(raw_input, _OLD_CONTENT_KEYS) or ""
            return ToolProjection(
                content=[
                    FileEditToolCallContent(
                        type="diff",
                        path=path,
                        old_text=old_text,
                        new_text=new_text,
                    )
                ],
                locations=[ToolCallLocation(path=path)],
                title=_tool_title(tool_name, path=path),
            )
        if tool_name in self.read_tool_names:
            path = _first_string(raw_input, _PATH_KEYS)
            if path is None:
                return None
            return ToolProjection(
                locations=[ToolCallLocation(path=path)],
                title=_tool_title(tool_name, path=path),
            )
        if tool_name in self.search_tool_names:
            location = _first_string(raw_input, _PATH_KEYS)
            search_term = _first_string(raw_input, _SEARCH_KEYS)
            locations = [ToolCallLocation(path=location)] if location is not None else None
            return ToolProjection(
                locations=locations,
                title=_search_title(tool_name, search_term=search_term, path=location),
            )
        return None

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
        del cwd
        if status != "completed":
            return None
        if tool_name in self.read_tool_names and _is_string_keyed_object_dict(raw_input):
            path = _first_string(raw_input, _PATH_KEYS)
            if path is None:
                return None
            return ToolProjection(
                content=[
                    FileEditToolCallContent(
                        type="diff",
                        path=path,
                        old_text="",
                        new_text=serialized_output,
                    )
                ],
                locations=[ToolCallLocation(path=path)],
                title=_tool_title(tool_name, path=path),
            )
        if tool_name in self.execute_tool_names:
            content: list[
                ContentToolCallContent | FileEditToolCallContent | TerminalToolCallContent
            ] = []
            terminal_id = _terminal_id(raw_output)
            if terminal_id is not None:
                content.append(TerminalToolCallContent(type="terminal", terminal_id=terminal_id))
            output_text = _output_text(raw_output, serialized_output)
            if output_text:
                content.append(
                    ContentToolCallContent(type="content", content=_text_block(output_text))
                )
            return ToolProjection(
                content=content or None,
                title=_command_title_from_input(raw_input),
            )
        return None


@dataclass(slots=True, frozen=True, kw_only=True)
class WebSearchProjectionMap:
    search_tool_names: frozenset[str] = _DEFAULT_WEB_SEARCH_TOOL_NAMES

    def project_start(
        self,
        tool_name: str,
        *,
        cwd: Path | None = None,
        raw_input: Any = None,
    ) -> ToolProjection | None:
        del cwd
        if tool_name not in self.search_tool_names:
            return None
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
        del cwd
        if status != "completed" or tool_name not in self.search_tool_names:
            return None
        query = _web_search_query(raw_input)
        return ToolProjection(
            content=[
                ContentToolCallContent(
                    type="content",
                    content=_text_block(_format_web_search_progress(raw_output, serialized_output)),
                )
            ],
            title=(
                f"Search web for {_single_line_preview(query, limit=_MAX_COMMAND_TITLE_CHARS)}"
                if query is not None
                else None
            ),
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class HttpRequestProjectionMap:
    fetch_tool_names: frozenset[str] = _DEFAULT_WEB_FETCH_TOOL_NAMES

    def project_start(
        self,
        tool_name: str,
        *,
        cwd: Path | None = None,
        raw_input: Any = None,
    ) -> ToolProjection | None:
        del cwd
        if tool_name not in self.fetch_tool_names:
            return None
        url = _web_fetch_url(raw_input)
        if url is None:
            return None
        method = _http_method_label(tool_name)
        return ToolProjection(
            content=[
                ContentToolCallContent(
                    type="content",
                    content=_text_block(_format_web_fetch_start(raw_input)),
                )
            ],
            title=f"{method} {_single_line_preview(url, limit=_MAX_COMMAND_TITLE_CHARS)}",
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
        del cwd
        if status != "completed" or tool_name not in self.fetch_tool_names:
            return None
        url = _web_fetch_url(raw_input)
        method = _http_method_label(tool_name)
        return ToolProjection(
            content=[
                ContentToolCallContent(
                    type="content",
                    content=_text_block(_format_web_fetch_progress(raw_output, serialized_output)),
                )
            ],
            title=(
                f"{method} {_single_line_preview(url, limit=_MAX_COMMAND_TITLE_CHARS)}"
                if url is not None
                else None
            ),
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class WebFetchProjectionMap(HttpRequestProjectionMap):
    """Backward-compatible alias for HTTP request-style projection presets."""


@dataclass(slots=True, frozen=True, kw_only=True)
class BrowserProjectionMap:
    navigate_tool_names: frozenset[str] = _DEFAULT_BROWSER_NAVIGATE_TOOL_NAMES
    read_tool_names: frozenset[str] = _DEFAULT_BROWSER_READ_TOOL_NAMES
    action_tool_names: frozenset[str] = _DEFAULT_BROWSER_ACTION_TOOL_NAMES

    def project_start(
        self,
        tool_name: str,
        *,
        cwd: Path | None = None,
        raw_input: Any = None,
    ) -> ToolProjection | None:
        del cwd
        if tool_name in self.navigate_tool_names:
            url = _web_fetch_url(raw_input)
            if url is None:
                return None
            return ToolProjection(
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=_text_block(f"URL: {url}"),
                    )
                ],
                title=f"Navigate {_single_line_preview(url, limit=_MAX_COMMAND_TITLE_CHARS)}",
            )
        if tool_name == "click_element" and _is_string_keyed_object_dict(raw_input):
            selector = _first_string(raw_input, ("selector",))
            if selector is None:
                return None
            return ToolProjection(
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=_text_block(f"Selector: {selector}"),
                    )
                ],
                title=f"Click {_single_line_preview(selector, limit=_MAX_COMMAND_TITLE_CHARS)}",
            )
        if tool_name == "get_elements" and _is_string_keyed_object_dict(raw_input):
            selector = _first_string(raw_input, ("selector",))
            return ToolProjection(
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=_text_block(
                            f"Selector: {selector}"
                            if selector is not None
                            else "Inspect page elements."
                        ),
                    )
                ],
                title=(
                    f"Inspect {_single_line_preview(selector, limit=_MAX_COMMAND_TITLE_CHARS)}"
                    if selector is not None
                    else "Inspect page elements"
                ),
            )
        if tool_name in self.read_tool_names:
            return ToolProjection(title=_browser_read_title(tool_name))
        if tool_name in self.action_tool_names:
            return ToolProjection(title=_browser_action_title(tool_name))
        return None

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
        del cwd
        if status != "completed":
            return None
        if tool_name in self.navigate_tool_names:
            return ToolProjection(
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=_text_block(_browser_text_preview(raw_output, serialized_output)),
                    )
                ],
                title=(
                    f"Navigate {_single_line_preview(url, limit=_MAX_COMMAND_TITLE_CHARS)}"
                    if (url := _web_fetch_url(raw_input)) is not None
                    else None
                ),
            )
        if tool_name == "extract_hyperlinks":
            return ToolProjection(
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=_text_block(
                            _format_browser_link_results(raw_output, serialized_output)
                        ),
                    )
                ],
                title="Extract hyperlinks",
            )
        if tool_name == "get_elements":
            return ToolProjection(
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=_text_block(
                            _format_browser_element_results(raw_output, serialized_output)
                        ),
                    )
                ],
                title="Inspect page elements",
            )
        if tool_name in self.read_tool_names or tool_name in self.action_tool_names:
            return ToolProjection(
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=_text_block(_browser_text_preview(raw_output, serialized_output)),
                    )
                ],
                title=_browser_progress_title(tool_name),
            )
        return None


@dataclass(slots=True, frozen=True, kw_only=True)
class CommandProjectionMap:
    execute_tool_names: frozenset[str] = _DEFAULT_COMMAND_TOOL_NAMES

    def project_start(
        self,
        tool_name: str,
        *,
        cwd: Path | None = None,
        raw_input: Any = None,
    ) -> ToolProjection | None:
        projection = FileSystemProjectionMap(
            execute_tool_names=self.execute_tool_names
        ).project_start(
            tool_name,
            cwd=cwd,
            raw_input=raw_input,
        )
        if projection is not None:
            return projection
        if tool_name in self.execute_tool_names:
            return ToolProjection(title="Run shell command")
        return None

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
        projection = FileSystemProjectionMap(
            execute_tool_names=self.execute_tool_names
        ).project_progress(
            tool_name,
            cwd=cwd,
            raw_input=raw_input,
            raw_output=raw_output,
            serialized_output=serialized_output,
            status=status,
        )
        if projection is not None:
            if projection.title is not None or tool_name not in self.execute_tool_names:
                return projection
            return ToolProjection(
                content=projection.content,
                locations=projection.locations,
                status=projection.status,
                title="Run shell command",
            )
        return None


@dataclass(slots=True, frozen=True, kw_only=True)
class CommunityFileManagementProjectionMap:
    read_tool_names: frozenset[str] = _DEFAULT_FILE_MANAGEMENT_READ_TOOL_NAMES
    write_tool_names: frozenset[str] = _DEFAULT_FILE_MANAGEMENT_WRITE_TOOL_NAMES
    search_tool_names: frozenset[str] = _DEFAULT_FILE_MANAGEMENT_SEARCH_TOOL_NAMES
    mutation_tool_names: frozenset[str] = _DEFAULT_FILE_MANAGEMENT_MUTATION_TOOL_NAMES

    def project_start(
        self,
        tool_name: str,
        *,
        cwd: Path | None = None,
        raw_input: Any = None,
    ) -> ToolProjection | None:
        projection = FileSystemProjectionMap(
            read_tool_names=self.read_tool_names,
            write_tool_names=self.write_tool_names,
        ).project_start(
            tool_name,
            cwd=cwd,
            raw_input=raw_input,
        )
        if projection is not None:
            return projection
        if tool_name == "file_search" and _is_string_keyed_object_dict(raw_input):
            pattern = _first_string(raw_input, ("pattern",))
            directory = _first_string(raw_input, _DIRECTORY_PATH_KEYS) or "."
            lines = [f"Directory: {directory}"]
            if pattern is not None:
                lines.append(f"Pattern: {pattern}")
            return ToolProjection(
                content=[
                    ContentToolCallContent(type="content", content=_text_block("\n".join(lines)))
                ],
                locations=[ToolCallLocation(path=directory)],
                title=(
                    f"Search `{pattern}` in `{directory}`"
                    if pattern is not None
                    else f"Search files in `{directory}`"
                ),
            )
        if tool_name == "list_directory" and _is_string_keyed_object_dict(raw_input):
            directory = _first_string(raw_input, _DIRECTORY_PATH_KEYS) or "."
            return ToolProjection(
                locations=[ToolCallLocation(path=directory)],
                title=f"List `{directory}`",
            )
        if tool_name in {"copy_file", "move_file"} and _is_string_keyed_object_dict(raw_input):
            source_path = _first_string(raw_input, ("source_path",))
            destination_path = _first_string(raw_input, ("destination_path",))
            if source_path is None or destination_path is None:
                return None
            verb = "Copy" if tool_name == "copy_file" else "Move"
            return ToolProjection(
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=_text_block(f"From: {source_path}\nTo: {destination_path}"),
                    )
                ],
                locations=[
                    ToolCallLocation(path=source_path),
                    ToolCallLocation(path=destination_path),
                ],
                title=f"{verb} `{source_path}` -> `{destination_path}`",
            )
        if tool_name == "file_delete" and _is_string_keyed_object_dict(raw_input):
            path = _first_string(raw_input, ("file_path",))
            if path is None:
                return None
            return ToolProjection(
                locations=[ToolCallLocation(path=path)],
                title=f"Delete `{path}`",
            )
        return None

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
        projection = FileSystemProjectionMap(
            read_tool_names=self.read_tool_names,
            write_tool_names=self.write_tool_names,
        ).project_progress(
            tool_name,
            cwd=cwd,
            raw_input=raw_input,
            raw_output=raw_output,
            serialized_output=serialized_output,
            status=status,
        )
        if projection is not None:
            return projection
        if status != "completed":
            return None
        if tool_name == "file_search" and _is_string_keyed_object_dict(raw_input):
            directory = _first_string(raw_input, _DIRECTORY_PATH_KEYS) or "."
            pattern = _first_string(raw_input, ("pattern",))
            return ToolProjection(
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=_text_block(
                            _truncate_text(serialized_output, limit=_MAX_CONTENT_PREVIEW_CHARS)
                        ),
                    )
                ],
                locations=[ToolCallLocation(path=directory)],
                title=(
                    f"Search `{pattern}` in `{directory}`"
                    if pattern is not None
                    else f"Search files in `{directory}`"
                ),
            )
        if tool_name == "list_directory" and _is_string_keyed_object_dict(raw_input):
            directory = _first_string(raw_input, _DIRECTORY_PATH_KEYS) or "."
            return ToolProjection(
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=_text_block(
                            _truncate_text(serialized_output, limit=_MAX_CONTENT_PREVIEW_CHARS)
                        ),
                    )
                ],
                locations=[ToolCallLocation(path=directory)],
                title=f"List `{directory}`",
            )
        if tool_name in self.mutation_tool_names and serialized_output:
            return ToolProjection(
                title=_file_management_mutation_title(tool_name, raw_input=raw_input),
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=_text_block(
                            _truncate_text(serialized_output, limit=_MAX_CONTENT_PREVIEW_CHARS)
                        ),
                    )
                ],
                locations=_file_management_locations(raw_input),
            )
        return None


@dataclass(slots=True, frozen=True, kw_only=True)
class FinanceProjectionMap:
    search_tool_names: frozenset[str] = _DEFAULT_FINANCE_SEARCH_TOOL_NAMES
    dataset_tool_names: frozenset[str] = _DEFAULT_FINANCE_DATASET_TOOL_NAMES

    def project_start(
        self,
        tool_name: str,
        *,
        cwd: Path | None = None,
        raw_input: Any = None,
    ) -> ToolProjection | None:
        del cwd
        if tool_name in self.search_tool_names:
            query = _finance_query(raw_input)
            return ToolProjection(
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=_text_block(
                            f"Query: {query}" if query is not None else "Lookup financial data."
                        ),
                    )
                ],
                title=(f"Search finance for {query}" if query is not None else "Search finance"),
            )
        if tool_name in self.dataset_tool_names:
            title = _finance_dataset_title(tool_name, raw_input=raw_input)
            return ToolProjection(title=title)
        return None

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
        del cwd
        if status != "completed":
            return None
        if tool_name in self.search_tool_names:
            query = _finance_query(raw_input)
            return ToolProjection(
                title=(f"Search finance for {query}" if query is not None else "Search finance"),
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=_text_block(
                            _truncate_text(
                                _output_text(raw_output, serialized_output),
                                limit=_MAX_CONTENT_PREVIEW_CHARS,
                            )
                        ),
                    )
                ],
            )
        if tool_name in self.dataset_tool_names:
            return ToolProjection(
                title=_finance_dataset_title(tool_name, raw_input=raw_input),
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=_text_block(
                            _truncate_text(
                                _output_text(raw_output, serialized_output),
                                limit=_MAX_CONTENT_PREVIEW_CHARS,
                            )
                        ),
                    )
                ],
            )
        return None


@dataclass(slots=True, frozen=True, kw_only=True)
class DeepAgentsProjectionMap:
    base: FileSystemProjectionMap = field(
        default_factory=lambda: FileSystemProjectionMap(
            read_tool_names=frozenset({"read_file"}),
            write_tool_names=frozenset({"edit_file", "write_file"}),
            search_tool_names=frozenset({"glob", "grep", "ls"}),
            execute_tool_names=frozenset({"execute"}),
        )
    )

    def project_start(
        self,
        tool_name: str,
        *,
        cwd: Path | None = None,
        raw_input: Any = None,
    ) -> ToolProjection | None:
        return self.base.project_start(tool_name, cwd=cwd, raw_input=raw_input)

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
        return self.base.project_progress(
            tool_name,
            cwd=cwd,
            raw_input=raw_input,
            raw_output=raw_output,
            serialized_output=serialized_output,
            status=status,
        )


def extract_tool_call_locations(raw_input: Any) -> list[ToolCallLocation]:
    if not _is_string_keyed_object_dict(raw_input):
        return []
    path = _first_string(raw_input, _PATH_KEYS)
    if path is None:
        return []
    return [ToolCallLocation(path=path)]


def build_tool_start_update(
    *,
    tool_call_id: str,
    tool_name: str,
    classifier: ToolClassifier,
    raw_input: Any,
    cwd: Path | None,
    projection_map: ProjectionMap | None,
) -> ToolCallStart:
    projected = (
        projection_map.project_start(tool_name, cwd=cwd, raw_input=raw_input)
        if projection_map
        else None
    )
    return ToolCallStart(
        session_update="tool_call",
        tool_call_id=tool_call_id,
        title=(projected.title if projected is not None else None) or tool_name,
        kind=classifier.classify(tool_name, raw_input),
        locations=(
            projected.locations
            if projected is not None and projected.locations is not None
            else None
        )
        or extract_tool_call_locations(raw_input)
        or None,
        raw_input=raw_input,
        status=(projected.status if projected is not None else None) or "in_progress",
        content=projected.content if projected is not None else None,
    )


def build_tool_progress_update(
    *,
    tool_call_id: str,
    tool_name: str,
    classifier: ToolClassifier,
    raw_input: Any,
    raw_output: Any,
    serialized_output: str,
    cwd: Path | None,
    projection_map: ProjectionMap | None,
    status: ToolCallStatus,
) -> ToolCallProgress:
    projected = (
        projection_map.project_progress(
            tool_name,
            cwd=cwd,
            raw_input=raw_input,
            raw_output=raw_output,
            serialized_output=serialized_output,
            status=status,
        )
        if projection_map
        else None
    )
    return ToolCallProgress(
        session_update="tool_call_update",
        tool_call_id=tool_call_id,
        title=projected.title if projected is not None else None,
        kind=classifier.classify(tool_name, raw_input),
        locations=(
            projected.locations
            if projected is not None and projected.locations is not None
            else None
        )
        or extract_tool_call_locations(raw_input)
        or None,
        raw_input=raw_input,
        raw_output=raw_output,
        status=(projected.status if projected is not None else None) or status,
        content=(
            projected.content
            if projected is not None and projected.content is not None
            else (
                [ContentToolCallContent(type="content", content=_text_block(serialized_output))]
                if serialized_output
                else None
            )
        ),
    )


def _merge_tool_projections(
    projections: Iterable[ToolProjection | None],
) -> ToolProjection | None:
    merged_content: list[
        ContentToolCallContent | FileEditToolCallContent | TerminalToolCallContent
    ] = []
    merged_locations: list[ToolCallLocation] = []
    merged_title: str | None = None
    merged_status: ToolCallStatus | None = None
    found = False
    for projection in projections:
        if projection is None:
            continue
        found = True
        if projection.content is not None:
            merged_content.extend(projection.content)
        if projection.locations is not None:
            merged_locations.extend(projection.locations)
        if merged_title is None and projection.title is not None:
            merged_title = projection.title
        if merged_status is None and projection.status is not None:
            merged_status = projection.status
    if not found:
        return None
    return ToolProjection(
        content=merged_content or None,
        locations=merged_locations or None,
        title=merged_title,
        status=merged_status,
    )


def _text_block(text: str) -> TextContentBlock:
    return TextContentBlock(type="text", text=text)


def _first_string(value: dict[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str):
            return candidate
    return None


def _format_command_title(command: str) -> str:
    stripped = command.strip()
    if not stripped:
        return "command"
    return _single_line_preview(stripped, limit=_MAX_COMMAND_TITLE_CHARS)


def _single_line_preview(text: str, *, limit: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1]}…"


def _truncate_text(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def _http_method_label(tool_name: str) -> str:
    if tool_name.startswith("requests_"):
        return tool_name.removeprefix("requests_").upper()
    return "Fetch"


def _command_text(raw_input: dict[str, Any]) -> str | None:
    command = _first_string(raw_input, _COMMAND_KEYS)
    if command is not None:
        return command
    for key in _COMMAND_LIST_KEYS:
        candidate = raw_input.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
        if isinstance(candidate, list):
            commands = [
                item.strip() for item in candidate if isinstance(item, str) and item.strip()
            ]
            if commands:
                return "\n".join(commands)
    return None


def _command_risk_note(command: str) -> str | None:
    normalized = f" {command.strip().lower()} "
    if any(pattern in normalized for pattern in _RISKY_COMMAND_PATTERNS):
        return "Potentially risky command"
    return None


def _command_title_from_input(raw_input: Any) -> str | None:
    if not _is_string_keyed_object_dict(raw_input):
        return None
    command = _command_text(raw_input)
    if command is None:
        return None
    return _format_command_title(command)


def _tool_title(tool_name: str, *, path: str | None) -> str | None:
    if path is None:
        return None
    normalized = tool_name.lower()
    if "read" in normalized:
        return f"Read `{path}`"
    if "edit" in normalized or "patch" in normalized or "update" in normalized:
        return f"Edit `{path}`"
    if "write" in normalized or "save" in normalized:
        return f"Write `{path}`"
    return None


def _search_title(tool_name: str, *, search_term: str | None, path: str | None) -> str:
    normalized = tool_name.lower()
    if normalized == "grep" and search_term is not None:
        return f"Search `{search_term}`"
    if normalized == "ls":
        return f"List `{path}`" if path is not None else "List files"
    if normalized == "glob" and search_term is not None:
        return f"Glob `{search_term}`"
    return tool_name


def _output_text(raw_output: Any, serialized_output: str) -> str:
    if _is_string_keyed_object_dict(raw_output):
        for key in _OUTPUT_TEXT_KEYS:
            candidate = raw_output.get(key)
            if isinstance(candidate, str):
                return candidate
    return serialized_output


def _terminal_id(raw_output: Any) -> str | None:
    if not _is_string_keyed_object_dict(raw_output):
        return None
    return _first_string(raw_output, _TERMINAL_ID_KEYS)


@dataclass(slots=True, frozen=True, kw_only=True)
class _NormalizedSearchResult:
    title: str
    url: str | None = None
    snippet: str | None = None


def _parse_structured_value(value: str) -> Any:
    if not value.strip():
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass
    try:
        return ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return None


def _web_search_query(raw_input: Any) -> str | None:
    if isinstance(raw_input, str):
        parsed = _parse_structured_value(raw_input)
        if _is_string_keyed_object_dict(parsed):
            return _first_string(parsed, _WEB_SEARCH_QUERY_KEYS)
        return raw_input.strip() or None
    if not _is_string_keyed_object_dict(raw_input):
        return None
    return _first_string(raw_input, _WEB_SEARCH_QUERY_KEYS)


def _web_fetch_url(raw_input: Any) -> str | None:
    if isinstance(raw_input, str):
        stripped = raw_input.strip()
        if stripped.startswith(("http://", "https://")):
            return stripped
        parsed = _parse_structured_value(stripped)
        if _is_string_keyed_object_dict(parsed):
            return _web_fetch_url(parsed)
        return None
    if not _is_string_keyed_object_dict(raw_input):
        return None
    direct_url = _first_string(raw_input, _WEB_FETCH_URL_KEYS)
    if direct_url is not None:
        return direct_url
    for key in ("data", "input", "payload", "text"):
        nested = raw_input.get(key)
        if isinstance(nested, str):
            parsed = _parse_structured_value(nested)
            if _is_string_keyed_object_dict(parsed):
                return _web_fetch_url(parsed)
    return None


def _format_web_search_start(raw_input: Any) -> str:
    query = _web_search_query(raw_input)
    if query is None:
        return "Searching the web."
    return f"Query: {query}"


def _format_web_fetch_start(raw_input: Any) -> str:
    url = _web_fetch_url(raw_input)
    if url is None:
        return "Fetching web content."
    return f"URL: {url}"


def _format_web_search_progress(raw_output: Any, serialized_output: str) -> str:
    results = _normalized_search_results(raw_output)
    if not results:
        return _truncate_text(serialized_output, limit=_MAX_CONTENT_PREVIEW_CHARS)
    lines: list[str] = []
    for index, result in enumerate(results[:5], start=1):
        lines.append(f"{index}. {result.title}")
        if result.url is not None:
            lines.append(result.url)
        if result.snippet is not None:
            lines.append(_single_line_preview(result.snippet, limit=220))
        lines.append("")
    return "\n".join(lines).rstrip()


def _normalized_search_results(
    raw_output: Any,
) -> tuple[_NormalizedSearchResult, ...] | None:
    parsed_output = raw_output
    if isinstance(raw_output, str):
        parsed_output = _parse_structured_value(raw_output)
    result_rows = _search_result_rows(parsed_output)
    if result_rows is None:
        return None
    normalized: list[_NormalizedSearchResult] = []
    for index, row in enumerate(result_rows, start=1):
        normalized_row = _normalize_search_result_row(row, index=index)
        if normalized_row is not None:
            normalized.append(normalized_row)
    return tuple(normalized) if normalized else None


def _search_result_rows(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, list):
        rows = [item for item in value if _is_string_keyed_object_dict(item)]
        return rows or None
    if not _is_string_keyed_object_dict(value):
        return None
    for key in _SEARCH_RESULT_CONTAINER_KEYS:
        nested = value.get(key)
        if isinstance(nested, list):
            rows = [item for item in nested if _is_string_keyed_object_dict(item)]
            if rows:
                return rows
    if any(
        value.get(key) is not None for key in _SEARCH_RESULT_TITLE_KEYS + _SEARCH_RESULT_URL_KEYS
    ):
        return [value]
    return None


def _normalize_search_result_row(
    row: dict[str, Any],
    *,
    index: int,
) -> _NormalizedSearchResult | None:
    title = _first_string(row, _SEARCH_RESULT_TITLE_KEYS)
    url = _first_string(row, _SEARCH_RESULT_URL_KEYS)
    snippet = _first_string(row, _SEARCH_RESULT_SNIPPET_KEYS)
    if title is None and url is None and snippet is None:
        return None
    return _NormalizedSearchResult(
        title=title or f"Result {index}",
        url=url,
        snippet=snippet,
    )


def _format_web_fetch_progress(raw_output: Any, serialized_output: str) -> str:
    parsed_output = raw_output
    if isinstance(raw_output, str):
        parsed_output = _parse_structured_value(raw_output)
    if not _is_string_keyed_object_dict(parsed_output):
        return _truncate_text(serialized_output, limit=_MAX_CONTENT_PREVIEW_CHARS)

    lines: list[str] = []
    url = _first_string(parsed_output, _WEB_FETCH_URL_KEYS)
    if url is not None:
        lines.append(f"URL: {url}")
    title = _first_string(parsed_output, _FETCH_RESULT_TITLE_KEYS)
    if title is not None:
        lines.append(f"Title: {title}")
    content = _first_string(parsed_output, _FETCH_RESULT_CONTENT_KEYS)
    if content is not None:
        lines.extend(("", "Preview:", _truncate_text(content, limit=_MAX_CONTENT_PREVIEW_CHARS)))
    if lines:
        return "\n".join(lines)
    return _truncate_text(serialized_output, limit=_MAX_CONTENT_PREVIEW_CHARS)


def _browser_read_title(tool_name: str) -> str:
    if tool_name == "current_webpage":
        return "Read current page"
    if tool_name == "extract_text":
        return "Extract page text"
    if tool_name == "extract_hyperlinks":
        return "Extract hyperlinks"
    if tool_name == "get_elements":
        return "Inspect page elements"
    return tool_name


def _browser_action_title(tool_name: str) -> str:
    if tool_name == "click_element":
        return "Click element"
    if tool_name == "previous_webpage":
        return "Navigate back"
    return tool_name


def _browser_progress_title(tool_name: str) -> str:
    if tool_name in _DEFAULT_BROWSER_READ_TOOL_NAMES:
        return _browser_read_title(tool_name)
    if tool_name in _DEFAULT_BROWSER_ACTION_TOOL_NAMES:
        return _browser_action_title(tool_name)
    return tool_name


def _file_management_locations(raw_input: Any) -> list[ToolCallLocation] | None:
    if not _is_string_keyed_object_dict(raw_input):
        return None
    locations: list[ToolCallLocation] = []
    for key in ("file_path", "source_path", "destination_path", "dir_path"):
        path = raw_input.get(key)
        if isinstance(path, str):
            locations.append(ToolCallLocation(path=path))
    return locations or None


def _file_management_mutation_title(tool_name: str, *, raw_input: Any) -> str | None:
    if not _is_string_keyed_object_dict(raw_input):
        return None
    if tool_name == "copy_file":
        source_path = _first_string(raw_input, ("source_path",))
        destination_path = _first_string(raw_input, ("destination_path",))
        if source_path is not None and destination_path is not None:
            return f"Copy `{source_path}` -> `{destination_path}`"
    if tool_name == "move_file":
        source_path = _first_string(raw_input, ("source_path",))
        destination_path = _first_string(raw_input, ("destination_path",))
        if source_path is not None and destination_path is not None:
            return f"Move `{source_path}` -> `{destination_path}`"
    if tool_name == "file_delete":
        path = _first_string(raw_input, ("file_path",))
        if path is not None:
            return f"Delete `{path}`"
    return None


def _finance_query(raw_input: Any) -> str | None:
    if isinstance(raw_input, str):
        parsed = _parse_structured_value(raw_input)
        if _is_string_keyed_object_dict(parsed):
            return _first_string(parsed, _FINANCE_QUERY_KEYS)
        return raw_input.strip() or None
    if not _is_string_keyed_object_dict(raw_input):
        return None
    return _first_string(raw_input, _FINANCE_QUERY_KEYS)


def _finance_dataset_title(tool_name: str, *, raw_input: Any) -> str:
    ticker = _finance_query(raw_input)
    period: str | None = None
    if _is_string_keyed_object_dict(raw_input):
        period = _first_string(raw_input, _FINANCE_PERIOD_KEYS)
    label = tool_name.replace("_", " ")
    if label == "balance sheets":
        label = "balance sheets"
    if ticker is None:
        return f"Get {label}"
    if period is None:
        return f"Get {label} for {ticker}"
    return f"Get {label} for {ticker} ({period})"


def _browser_text_preview(raw_output: Any, serialized_output: str) -> str:
    if isinstance(raw_output, str):
        return _truncate_text(raw_output, limit=_MAX_CONTENT_PREVIEW_CHARS)
    return _truncate_text(serialized_output, limit=_MAX_CONTENT_PREVIEW_CHARS)


def _format_browser_link_results(raw_output: Any, serialized_output: str) -> str:
    parsed_output = raw_output
    if isinstance(raw_output, str):
        parsed_output = _parse_structured_value(raw_output)
    if not isinstance(parsed_output, list):
        return _truncate_text(serialized_output, limit=_MAX_CONTENT_PREVIEW_CHARS)
    lines: list[str] = []
    for index, value in enumerate(parsed_output[:10], start=1):
        if isinstance(value, str) and value:
            lines.append(f"{index}. {value}")
    return (
        "\n".join(lines)
        if lines
        else _truncate_text(serialized_output, limit=_MAX_CONTENT_PREVIEW_CHARS)
    )


def _format_browser_element_results(raw_output: Any, serialized_output: str) -> str:
    parsed_output = raw_output
    if isinstance(raw_output, str):
        parsed_output = _parse_structured_value(raw_output)
    if not isinstance(parsed_output, list):
        return _truncate_text(serialized_output, limit=_MAX_CONTENT_PREVIEW_CHARS)
    lines: list[str] = []
    for index, value in enumerate(parsed_output[:10], start=1):
        if not _is_string_keyed_object_dict(value):
            continue
        text = _first_string(value, ("text", "value", "name"))
        selector = _first_string(value, ("selector", "xpath"))
        if text is not None and selector is not None:
            lines.append(f"{index}. {text} ({selector})")
        elif text is not None:
            lines.append(f"{index}. {text}")
        elif selector is not None:
            lines.append(f"{index}. {selector}")
    return (
        "\n".join(lines)
        if lines
        else _truncate_text(serialized_output, limit=_MAX_CONTENT_PREVIEW_CHARS)
    )
