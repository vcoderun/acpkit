from __future__ import annotations as _annotations

from collections.abc import Sequence
from pathlib import Path

from .host import HostAccessPolicy

__all__ = ("caution_for_command", "caution_for_path")


def caution_for_path(
    path: str | Path,
    *,
    session_cwd: Path,
    workspace_root: Path | None = None,
    access_policy: HostAccessPolicy | None = None,
) -> str | None:
    policy = access_policy or HostAccessPolicy()
    evaluation = policy.evaluate_path(
        path,
        session_cwd=session_cwd,
        workspace_root=workspace_root,
    )
    if not evaluation.has_risks:
        return None
    return evaluation.message


def caution_for_command(
    command: str,
    *,
    args: Sequence[str] | None = None,
    cwd: str | Path | None = None,
    session_cwd: Path,
    workspace_root: Path | None = None,
    access_policy: HostAccessPolicy | None = None,
) -> str | None:
    policy = access_policy or HostAccessPolicy()
    evaluation = policy.evaluate_command(
        command,
        args=args,
        cwd=cwd,
        session_cwd=session_cwd,
        workspace_root=workspace_root,
    )
    if not evaluation.has_risks:
        return None
    return evaluation.message
