from __future__ import annotations as _annotations

from collections.abc import Sequence
from difflib import unified_diff
from pathlib import Path

DEFAULT_TEXT_TRUNCATION_MARKER = "\n\n...[truncated]"

__all__ = (
    "DEFAULT_TEXT_TRUNCATION_MARKER",
    "format_code_block",
    "format_diff_preview",
    "format_terminal_status",
    "single_line_summary",
    "truncate_lines",
    "truncate_text",
)


def truncate_text(
    text: str,
    *,
    limit: int,
    marker: str = DEFAULT_TEXT_TRUNCATION_MARKER,
) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= len(marker):
        return f"{text[:limit]}{marker}"
    return f"{text[: limit - len(marker)]}{marker}"


def truncate_lines(
    lines: Sequence[str],
    *,
    max_lines: int,
    truncation_line: str = "... [truncated]",
) -> list[str]:
    if max_lines <= 0:
        return []
    materialized = list(lines)
    if len(materialized) <= max_lines:
        return materialized
    if max_lines == 1:
        return [truncation_line]
    return [*materialized[: max_lines - 1], truncation_line]


def single_line_summary(
    text: str,
    *,
    limit: int,
) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit].rstrip()}..."


def format_code_block(
    text: str,
    *,
    language: str | None = None,
    limit: int | None = None,
) -> str:
    body = truncate_text(text, limit=limit) if limit is not None else text
    if language is None:
        return f"```\n{body}\n```"
    return f"```{language}\n{body}\n```"


def format_diff_preview(
    path: str | Path,
    old_text: str,
    new_text: str,
    *,
    context_lines: int = 3,
    max_lines: int = 40,
    include_path_header: bool = True,
    include_diff_headers: bool = False,
) -> str:
    diff_lines = list(
        unified_diff(
            old_text.strip().splitlines(),
            new_text.strip().splitlines(),
            lineterm="",
            n=context_lines,
        )
    )
    if not include_diff_headers:
        diff_lines = [
            line
            for line in diff_lines
            if not line.startswith("--- ") and not line.startswith("+++ ")
        ]
    if not diff_lines:
        diff_lines = ["(no visible changes)"]
    body_lines: list[str] = []
    if include_path_header:
        body_lines.append(f"# {path}")
    body_lines.extend(truncate_lines(diff_lines, max_lines=max_lines))
    return "\n".join(body_lines)


def format_terminal_status(
    *,
    exit_code: int | None,
    signal: str | None,
) -> str:
    if signal is not None:
        return f"cancelled ({signal})"
    if exit_code is None:
        return "running"
    if exit_code == 0:
        return "ok (0)"
    return f"fail ({exit_code})"
