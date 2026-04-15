from __future__ import annotations as _annotations

from pathlib import Path

__all__ = ("normalize_host_path", "path_is_within_root")


def normalize_host_path(
    path: str | Path,
    *,
    base_dir: Path,
) -> tuple[Path, bool]:
    candidate = Path(path).expanduser()
    is_absolute_input = candidate.is_absolute()
    resolved_candidate = candidate if is_absolute_input else base_dir / candidate
    return resolved_candidate.resolve(strict=False), is_absolute_input


def path_is_within_root(
    path: Path,
    root: Path,
) -> bool:
    normalized_root = root.resolve(strict=False)
    try:
        path.relative_to(normalized_root)
    except ValueError:
        return False
    return True
