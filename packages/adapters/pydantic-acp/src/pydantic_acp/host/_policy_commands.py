from __future__ import annotations as _annotations

from collections.abc import Sequence
from pathlib import Path

from ._policy_paths import normalize_host_path

__all__ = ("extract_command_path_candidates", "resolve_command_cwd")


def resolve_command_cwd(
    cwd: str | Path | None,
    *,
    session_cwd: Path,
) -> Path:
    if cwd is None:
        return session_cwd.resolve(strict=False)
    return normalize_host_path(cwd, base_dir=session_cwd)[0]


def extract_command_path_candidates(
    command: str,
    *,
    args: Sequence[str] | None,
    command_cwd: Path,
) -> tuple[Path, ...]:
    candidates: list[Path] = []
    _append_path_candidate(candidates, command, base_dir=command_cwd)
    for arg in args or ():
        token = _extract_path_token(arg)
        if token is None:
            continue
        _append_path_candidate(candidates, token, base_dir=command_cwd)
    return tuple(_dedupe_paths(candidates))


def _append_path_candidate(
    candidates: list[Path],
    token: str,
    *,
    base_dir: Path,
) -> None:
    if not _looks_like_path_token(token):
        return
    candidates.append(normalize_host_path(token, base_dir=base_dir)[0])


def _extract_path_token(token: str) -> str | None:
    if _looks_like_path_token(token):
        return token
    if token.startswith("--") and "=" in token:
        _, value = token.split("=", 1)
        if _looks_like_path_token(value):
            return value
    if "=" in token and not token.startswith("-"):
        _, value = token.split("=", 1)
        if _looks_like_path_token(value):
            return value
    return None


def _looks_like_path_token(token: str) -> bool:
    if not token:
        return False
    if token in {".", ".."}:
        return True
    if token.startswith("-"):
        return False
    return (
        token.startswith(("/", "./", "../", "~/", ".\\", "..\\")) or "/" in token or "\\" in token
    )


def _dedupe_paths(paths: Sequence[Path]) -> list[Path]:
    seen: set[Path] = set()
    deduped: list[Path] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped
