from __future__ import annotations as _annotations

import sys
from pathlib import Path

import pytest


def _prepend_path(path: Path) -> None:
    resolved = str(path.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)


ROOT = Path(__file__).resolve().parents[1]

_prepend_path(ROOT / "packages" / "helpers" / "codex-auth-helper" / "src")
_prepend_path(ROOT / "packages" / "adapters" / "langchain-acp" / "src")
_prepend_path(ROOT / "packages" / "adapters" / "pydantic-acp" / "src")
_prepend_path(ROOT / "src")
_prepend_path(ROOT.parent / "vcoderun" / "src")


def pytest_addoption(parser: pytest.Parser) -> None:
    # Some local commands disable plugin autoload, which prevents pytest-asyncio
    # from registering its ini option before config validation runs.
    parser.addini(
        "asyncio_mode",
        "Compatibility shim for pytest-asyncio config when plugin autoload is disabled.",
        default="auto",
    )
