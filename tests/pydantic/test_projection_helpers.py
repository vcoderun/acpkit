from __future__ import annotations as _annotations

from pydantic_acp import (
    HostAccessPolicy,
    caution_for_command,
    caution_for_path,
    format_code_block,
    format_diff_preview,
    format_terminal_status,
    single_line_summary,
    truncate_lines,
    truncate_text,
)

from .support import Path


def test_truncate_text_appends_default_marker() -> None:
    assert truncate_text("x" * 5000, limit=20).endswith("...[truncated]")


def test_truncate_lines_keeps_last_line_for_marker() -> None:
    lines = truncate_lines(["a", "b", "c", "d"], max_lines=3)

    assert lines == ["a", "b", "... [truncated]"]


def test_single_line_summary_normalizes_newlines() -> None:
    assert single_line_summary("hello\nworld", limit=20) == "hello world"


def test_format_code_block_supports_language() -> None:
    rendered = format_code_block("echo hi", language="bash")

    assert rendered == "```bash\necho hi\n```"


def test_format_diff_preview_hides_unified_headers() -> None:
    rendered = format_diff_preview("README.md", "# old\n", "# new\n")

    assert rendered.startswith("# README.md")
    assert "--- " not in rendered
    assert "+++ " not in rendered


def test_format_terminal_status_returns_expected_labels() -> None:
    assert format_terminal_status(exit_code=0, signal=None) == "ok (0)"
    assert format_terminal_status(exit_code=2, signal=None) == "fail (2)"
    assert format_terminal_status(exit_code=None, signal="SIGTERM") == "cancelled (SIGTERM)"
    assert format_terminal_status(exit_code=None, signal=None) == "running"


def test_caution_for_path_uses_host_access_policy() -> None:
    session_cwd = Path("/tmp/acpkit-projection-helpers").resolve()

    caution = caution_for_path(
        "../outside.txt",
        session_cwd=session_cwd,
        workspace_root=session_cwd,
        access_policy=HostAccessPolicy(),
    )

    assert caution is not None
    assert "workspace root" in caution


def test_caution_for_command_uses_host_access_policy() -> None:
    session_cwd = Path("/tmp/acpkit-projection-helpers").resolve()

    caution = caution_for_command(
        "python",
        args=["../scripts/build.py"],
        session_cwd=session_cwd,
        access_policy=HostAccessPolicy.strict(),
    )

    assert caution is not None
    assert "outside the active session cwd" in caution


def test_projection_helpers_cover_edge_cases_and_no_risk_paths() -> None:
    session_cwd = Path("/tmp/acpkit-projection-helpers").resolve()

    assert truncate_text("abcdef", limit=0) == ""
    assert truncate_text("abcdef", limit=3, marker="[...]") == "abc[...]"
    assert truncate_lines(["a", "b"], max_lines=0) == []
    assert truncate_lines(["a", "b"], max_lines=1) == ["... [truncated]"]
    assert single_line_summary("hello   world  again", limit=8) == "hello wo..."
    assert format_code_block("abcdef", limit=4) == "```\nabcd\n\n...[truncated]\n```"
    assert (
        format_diff_preview(
            "README.md",
            "# same\n",
            "# same\n",
            include_path_header=False,
        )
        == "(no visible changes)"
    )

    rendered = format_diff_preview(
        "README.md",
        "# old\n",
        "# new\n",
        include_path_header=False,
        include_diff_headers=True,
    )

    assert rendered.startswith("--- ")
    assert "+++ " in rendered
    assert (
        caution_for_path(
            "notes.txt",
            session_cwd=session_cwd,
            workspace_root=session_cwd,
        )
        is None
    )
    assert (
        caution_for_command(
            "python",
            args=["./script.py"],
            session_cwd=session_cwd,
            workspace_root=session_cwd,
        )
        is None
    )
