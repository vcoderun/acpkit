from __future__ import annotations as _annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final, Literal

HookStage = Literal["pre-commit", "manual"]

MAJOR_PREFIXES: Final[tuple[str, ...]] = (
    "src/",
    "packages/",
    "tests/",
    "scripts/",
    ".github/workflows/",
)

MAJOR_EXACT_PATHS: Final[frozenset[str]] = frozenset(
    {
        "pyproject.toml",
        "Makefile",
        ".pre-commit-config.yaml",
        "uv.lock",
    }
)


@dataclass(frozen=True, slots=True)
class ChangeSelection:
    stage: HookStage
    changed_files: tuple[str, ...]
    major_files: tuple[str, ...]


def _parse_args() -> tuple[HookStage, list[str]]:
    parser = argparse.ArgumentParser(
        description="Run an expensive command only when the staged change set is major."
    )
    parser.add_argument(
        "--stage",
        choices=("pre-commit", "manual"),
        default="pre-commit",
        help="Hook stage that is invoking the gate.",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to run after '--'.",
    )
    namespace = parser.parse_args()
    command = namespace.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise SystemExit("Expected a command after '--'.")
    return namespace.stage, command


def _git_changed_files() -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        check=True,
        text=True,
        capture_output=True,
    )
    return tuple(line for line in result.stdout.splitlines() if line)


def _is_major_path(path: str) -> bool:
    normalized_path = PurePosixPath(path).as_posix()
    if normalized_path in MAJOR_EXACT_PATHS:
        return True
    return any(normalized_path.startswith(prefix) for prefix in MAJOR_PREFIXES)


def _select_changes(stage: HookStage) -> ChangeSelection:
    changed_files = _git_changed_files()
    major_files = tuple(path for path in changed_files if _is_major_path(path))
    return ChangeSelection(
        stage=stage,
        changed_files=changed_files,
        major_files=major_files,
    )


def _should_run(selection: ChangeSelection) -> bool:
    if selection.stage == "manual":
        return True
    if os.environ.get("ACPKIT_FORCE_MAJOR_HOOKS") == "1":
        return True
    return bool(selection.major_files)


def _print_skip_message(selection: ChangeSelection, command: list[str]) -> None:
    if not selection.changed_files:
        print(f"Skipping {' '.join(command)}: no staged files.")
        return
    print(
        f"Skipping {' '.join(command)}: no major staged changes "
        f"among {len(selection.changed_files)} staged file(s)."
    )


def main() -> None:
    stage, command = _parse_args()
    selection = _select_changes(stage)
    if not _should_run(selection):
        _print_skip_message(selection, command)
        return

    if selection.stage == "pre-commit":
        major_files = ", ".join(selection.major_files)
        print(f"Major staged changes detected: {major_files}")

    completed = subprocess.run(command, check=False)
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
