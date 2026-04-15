from __future__ import annotations as _annotations

from ._projection_risk import caution_for_command, caution_for_path
from ._projection_text import (
    DEFAULT_TEXT_TRUNCATION_MARKER,
    format_code_block,
    format_diff_preview,
    format_terminal_status,
    single_line_summary,
    truncate_lines,
    truncate_text,
)

__all__ = (
    "DEFAULT_TEXT_TRUNCATION_MARKER",
    "caution_for_command",
    "caution_for_path",
    "format_code_block",
    "format_diff_preview",
    "format_terminal_status",
    "single_line_summary",
    "truncate_lines",
    "truncate_text",
)
