from __future__ import annotations as _annotations

import sys
from pathlib import Path


def _prepend_path(path: Path) -> None:
    resolved = str(path.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)


ROOT = Path(__file__).resolve().parents[1]

_prepend_path(ROOT / "helpers" / "codex-auth-helper" / "src")
_prepend_path(ROOT / "packages" / "pydantic-acp" / "src")
_prepend_path(ROOT / "src")
_prepend_path(ROOT.parent / "vcoderun" / "src")
