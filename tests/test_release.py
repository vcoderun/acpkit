from __future__ import annotations as _annotations

import subprocess
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from shutil import copy2

import acpkit

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "release.py"
_BUMP_SCRIPT = _ROOT / "bump.sh"
_BUMP_VERSION_FILES = (
    Path("src/acpkit/_version.py"),
    Path("packages/adapters/langchain-acp/src/langchain_acp/_version.py"),
    Path("packages/adapters/pydantic-acp/src/pydantic_acp/_version.py"),
    Path("packages/helpers/codex-auth-helper/src/codex_auth_helper/_version.py"),
    Path("packages/transports/acpremote/src/acpremote/_version.py"),
    Path("VERSION"),
    Path("packages/adapters/langchain-acp/VERSION"),
    Path("packages/adapters/pydantic-acp/VERSION"),
    Path("packages/helpers/codex-auth-helper/VERSION"),
    Path("packages/transports/acpremote/VERSION"),
)
_PUBLISH_WORKFLOW = _ROOT / ".github" / "workflows" / "publish.yml"


def _run_release(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        cwd=_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_release_metadata_matches_current_version_and_tag() -> None:
    result = _run_release("check", "--tag", f"v{acpkit.__version__}")

    assert result.returncode == 0
    assert f"Release metadata valid for {acpkit.__version__}." in result.stdout


def test_release_metadata_accepts_dated_tag() -> None:
    result = _run_release("check", "--tag", f"v{acpkit.__version__}_2026-07-04")

    assert result.returncode == 0
    assert f"Release metadata valid for {acpkit.__version__}." in result.stdout


def test_release_metadata_rejects_mismatched_tag() -> None:
    result = _run_release("check", "--tag", "v999.0.0")

    assert result.returncode == 1
    assert "does not match workspace version" in result.stderr


def test_release_metadata_rejects_invalid_release_date() -> None:
    result = _run_release("check", "--tag", f"v{acpkit.__version__}_2026-02-30")

    assert result.returncode == 1
    assert "does not match workspace version" in result.stderr


def test_publish_workflow_requires_a_published_github_release() -> None:
    workflow = _PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    assert "release:\n    types: [published]" in workflow
    assert "push:\n    tags:" not in workflow
    assert "RELEASE_TAG: ${{ github.event.release.tag_name }}" in workflow
    assert 'make release RELEASE_TAG="$RELEASE_TAG"' in workflow
    assert "github.event.release.tag_name" in workflow


def test_bump_script_updates_version_files_and_root_extras(tmp_path: Path) -> None:
    for relative_path in (*_BUMP_VERSION_FILES, Path("pyproject.toml")):
        source = _ROOT / relative_path
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        copy2(source, destination)
    copy2(_BUMP_SCRIPT, tmp_path / "bump.sh")

    result = subprocess.run(
        ["bash", "bump.sh", "1.2.3"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    for relative_path in _BUMP_VERSION_FILES:
        content = (tmp_path / relative_path).read_text(encoding="utf-8")
        if relative_path.name == "_version.py":
            assert '__version__ = "1.2.3"' in content
        else:
            assert content == "1.2.3\n"

    optional = _root_optional_dependencies(tmp_path)
    assert optional["codex"] == ["codex-auth-helper>=1.2.3,<2.0.0"]
    assert optional["deepagents"] == ["langchain-acp[deepagents]>=1.2.3,<2.0.0"]
    assert optional["langchain"] == ["langchain-acp>=1.2.3,<2.0.0"]
    assert optional["pydantic"] == ["pydantic-acp>=1.2.3,<2.0.0"]
    assert optional["remote"] == ["acpremote>=1.2.3,<2.0.0"]


def _root_optional_dependencies(root: Path) -> Mapping[str, list[str]]:
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    optional = metadata["project"]["optional-dependencies"]
    assert isinstance(optional, dict)
    return optional
