from __future__ import annotations as _annotations

import argparse
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass
from datetime import date
from email.message import Message
from email.parser import Parser
from pathlib import Path
from typing import Final

__all__ = ("main",)

_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
_VERSION_PATTERN: Final[re.Pattern[str]] = re.compile(r'__version__\s*=\s*"(?P<version>[^"]+)"')
_SUPPORTED_VERSION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:(?:a|b|rc)\d+)?$",
)
_RELEASE_DATE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\d{4}(?:-\d{2}-\d{2}|_\d{2}_\d{2})$",
)


class ReleaseValidationError(RuntimeError):
    """Raised when release metadata or artifacts violate workspace invariants."""


@dataclass(frozen=True, slots=True)
class Project:
    name: str
    directory: Path
    version_file: Path


_PROJECTS: Final[tuple[Project, ...]] = (
    Project(
        name="codex-auth-helper",
        directory=Path("packages/helpers/codex-auth-helper"),
        version_file=Path("packages/helpers/codex-auth-helper/src/codex_auth_helper/_version.py"),
    ),
    Project(
        name="pydantic-acp",
        directory=Path("packages/adapters/pydantic-acp"),
        version_file=Path("packages/adapters/pydantic-acp/src/pydantic_acp/_version.py"),
    ),
    Project(
        name="langchain-acp",
        directory=Path("packages/adapters/langchain-acp"),
        version_file=Path("packages/adapters/langchain-acp/src/langchain_acp/_version.py"),
    ),
    Project(
        name="acpremote",
        directory=Path("packages/transports/acpremote"),
        version_file=Path("packages/transports/acpremote/src/acpremote/_version.py"),
    ),
    Project(
        name="acpkit",
        directory=Path(),
        version_file=Path("src/acpkit/_version.py"),
    ),
)


def _read_version(path: Path) -> str:
    match = _VERSION_PATTERN.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise ReleaseValidationError(f"Could not read __version__ from {path}.")
    return match.group("version")


def _workspace_version(root: Path) -> str:
    versions = {project.name: _read_version(root / project.version_file) for project in _PROJECTS}
    unique_versions = set(versions.values())
    if len(unique_versions) != 1:
        rendered = ", ".join(f"{name}={version}" for name, version in versions.items())
        raise ReleaseValidationError(f"Workspace package versions are not synchronized: {rendered}")
    version = unique_versions.pop()
    if _SUPPORTED_VERSION_PATTERN.fullmatch(version) is None:
        raise ReleaseValidationError(
            f"Unsupported release version {version!r}; expected X.Y.Z or X.Y.ZrcN.",
        )
    return version


def _next_major(version: str) -> str:
    match = _SUPPORTED_VERSION_PATTERN.fullmatch(version)
    if match is None:
        raise ReleaseValidationError(f"Cannot derive the next major version from {version!r}.")
    return f"{int(match.group('major')) + 1}.0.0"


def _is_prerelease(version: str) -> bool:
    return re.search(r"(?:a|b|rc)\d+$", version) is not None


def _check_changelog(root: Path, version: str) -> None:
    changelog_path = root / "CHANGELOG.md"
    if not changelog_path.is_file():
        raise ReleaseValidationError("CHANGELOG.md is required for a release.")
    heading = re.compile(rf"^## \[{re.escape(version)}\](?:\s|$)", re.MULTILINE)
    if heading.search(changelog_path.read_text(encoding="utf-8")) is None:
        raise ReleaseValidationError(
            f"CHANGELOG.md does not contain a [{version}] release heading.",
        )


def _check_root_extras(root: Path, version: str) -> None:
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    optional = metadata["project"]["optional-dependencies"]
    specifier = (
        f"=={version}" if _is_prerelease(version) else f">={version},<{_next_major(version)}"
    )
    expected = {
        "codex": f"codex-auth-helper{specifier}",
        "deepagents": f"langchain-acp[deepagents]{specifier}",
        "langchain": f"langchain-acp{specifier}",
        "pydantic": f"pydantic-acp{specifier}",
        "remote": f"acpremote{specifier}",
    }
    for extra, requirement in expected.items():
        if requirement not in optional.get(extra, []):
            raise ReleaseValidationError(
                f"Root extra {extra!r} must require the synchronized package as {requirement!r}.",
            )


def _check_release_tag(tag: str, version: str) -> None:
    version_tag = f"v{version}"
    if tag == version_tag:
        return

    dated_prefix = f"{version_tag}_"
    if tag.startswith(dated_prefix):
        release_date = tag.removeprefix(dated_prefix)
        if _RELEASE_DATE_PATTERN.fullmatch(release_date) is not None:
            try:
                date.fromisoformat(release_date.replace("_", "-"))
            except ValueError:
                pass
            else:
                return

    raise ReleaseValidationError(
        f"Release tag {tag!r} does not match workspace version {version!r}; "
        f"expected {version_tag!r}, {version_tag + '_YYYY-MM-DD'!r}, "
        f"or {version_tag + '_YYYY_MM_DD'!r}.",
    )


def check_release(*, root: Path, tag: str | None) -> str:
    version = _workspace_version(root)
    _check_changelog(root, version)
    _check_root_extras(root, version)
    if tag is not None:
        _check_release_tag(tag, version)
    print(f"Release metadata valid for {version}.")
    return version


def _safe_output_directory(root: Path, output: Path) -> Path:
    resolved_root = root.resolve()
    resolved_output = (root / output).resolve() if not output.is_absolute() else output.resolve()
    if resolved_output == resolved_root or not resolved_output.is_relative_to(resolved_root):
        raise ReleaseValidationError("Artifact output directory must stay inside the repository.")
    return resolved_output


def _run(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def build_artifacts(*, root: Path, output: Path) -> Path:
    version = check_release(root=root, tag=None)
    output_dir = _safe_output_directory(root, output)
    shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True)
    for project in _PROJECTS:
        _run(
            [
                "uv",
                "build",
                "--project",
                str(root / project.directory),
                "--out-dir",
                str(output_dir),
            ],
            cwd=root,
        )
    _validate_artifacts(output_dir=output_dir, version=version)
    print(f"Built and validated release artifacts in {output_dir}.")
    return output_dir


def _message_from_wheel(path: Path) -> Message:
    with zipfile.ZipFile(path) as archive:
        metadata_names = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_names) != 1:
            raise ReleaseValidationError(f"{path.name} must contain exactly one METADATA file.")
        content = archive.read(metadata_names[0]).decode("utf-8")
    return Parser().parsestr(content)


def _message_from_sdist(path: Path) -> Message:
    with tarfile.open(path, mode="r:gz") as archive:
        metadata_members = [
            member for member in archive.getmembers() if member.name.endswith("/PKG-INFO")
        ]
        if len(metadata_members) != 1:
            raise ReleaseValidationError(f"{path.name} must contain exactly one PKG-INFO file.")
        extracted = archive.extractfile(metadata_members[0])
        if extracted is None:
            raise ReleaseValidationError(f"Could not read PKG-INFO from {path.name}.")
        content = extracted.read().decode("utf-8")
    return Parser().parsestr(content)


def _artifact_metadata(path: Path) -> tuple[str, Message]:
    if path.name.endswith(".whl"):
        return "wheel", _message_from_wheel(path)
    if path.name.endswith(".tar.gz"):
        return "sdist", _message_from_sdist(path)
    raise ReleaseValidationError(f"Unexpected artifact in release directory: {path.name}")


def _validate_root_requirements(metadata: Message, version: str) -> None:
    requirements = metadata.get_all("Requires-Dist", [])
    upper_bound = _next_major(version)
    for package_name in (
        "acpremote",
        "codex-auth-helper",
        "langchain-acp",
        "pydantic-acp",
    ):
        matching = [
            requirement
            for requirement in requirements
            if requirement.lower().startswith(package_name)
        ]
        if not matching:
            raise ReleaseValidationError(
                f"acpkit wheel does not declare its {package_name} integration dependency.",
            )
        if _is_prerelease(version):
            valid_requirement = any(f"=={version}" in requirement for requirement in matching)
        else:
            valid_requirement = any(
                f">={version}" in requirement and f"<{upper_bound}" in requirement
                for requirement in matching
            )
        if not valid_requirement:
            raise ReleaseValidationError(
                f"acpkit wheel does not constrain {package_name} to the synchronized release.",
            )


def _validate_artifacts(*, output_dir: Path, version: str) -> None:
    files = sorted(path for path in output_dir.iterdir() if path.is_file())
    unexpected = [
        path.name
        for path in files
        if not path.name.startswith(".")
        and not path.name.endswith(".whl")
        and not path.name.endswith(".tar.gz")
    ]
    if unexpected:
        raise ReleaseValidationError(
            f"Unexpected files in release directory: {', '.join(unexpected)}.",
        )
    artifacts = [
        path for path in files if path.name.endswith(".whl") or path.name.endswith(".tar.gz")
    ]
    expected_count = len(_PROJECTS) * 2
    if len(artifacts) != expected_count:
        raise ReleaseValidationError(
            f"Expected {expected_count} release artifacts, found {len(artifacts)}.",
        )
    observed: dict[str, set[str]] = {}
    root_wheel_metadata: Message | None = None
    known_names = {project.name for project in _PROJECTS}
    for artifact in artifacts:
        artifact_kind, metadata = _artifact_metadata(artifact)
        name = metadata.get("Name", "").lower()
        artifact_version = metadata.get("Version")
        if name not in known_names:
            raise ReleaseValidationError(f"Unexpected package name {name!r} in {artifact.name}.")
        if artifact_version != version:
            raise ReleaseValidationError(
                f"{artifact.name} reports version {artifact_version!r}, expected {version!r}.",
            )
        observed.setdefault(name, set()).add(artifact_kind)
        if name == "acpkit" and artifact_kind == "wheel":
            root_wheel_metadata = metadata
    expected_kinds = {"wheel", "sdist"}
    for project in _PROJECTS:
        if observed.get(project.name) != expected_kinds:
            raise ReleaseValidationError(
                f"{project.name} must provide exactly one wheel and one source distribution.",
            )
    if root_wheel_metadata is None:
        raise ReleaseValidationError("The acpkit wheel metadata was not found.")
    _validate_root_requirements(root_wheel_metadata, version)


def smoke_test_artifacts(*, root: Path, dist_dir: Path) -> None:
    version = check_release(root=root, tag=None)
    output_dir = _safe_output_directory(root, dist_dir)
    _validate_artifacts(output_dir=output_dir, version=version)
    with tempfile.TemporaryDirectory(prefix="acpkit-release-") as temporary_directory:
        venv_dir = Path(temporary_directory) / "venv"
        _run(["uv", "venv", "--python", sys.executable, str(venv_dir)], cwd=root)
        python = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        prerelease_arguments: list[str] = []
        package_requirements = [f"acpkit[all]=={version}"]
        if _is_prerelease(version):
            prerelease_arguments = ["--prerelease", "explicit"]
            package_requirements = [
                *(
                    f"{project.name}=={version}"
                    for project in _PROJECTS
                    if project.name != "acpkit"
                ),
                *package_requirements,
            ]
        _run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                "--find-links",
                str(output_dir),
                *prerelease_arguments,
                *package_requirements,
            ],
            cwd=root,
        )
        import_check = (
            "import acpkit, acpremote, codex_auth_helper, langchain_acp, pydantic_acp\n"
            f"expected = {version!r}\n"
            "modules = (acpkit, acpremote, codex_auth_helper, langchain_acp, pydantic_acp)\n"
            "assert all(module.__version__ == expected for module in modules)\n"
        )
        _run([str(python), "-c", import_check], cwd=root)
        _run([str(python), "-m", "acpkit", "--help"], cwd=root)
        _run([str(python), "-m", "acpremote", "--help"], cwd=root)
    print(f"Clean-environment artifact smoke test passed for {version}.")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and build synchronized ACP Kit releases.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="Validate release metadata.")
    check_parser.add_argument(
        "--tag",
        help="Expected git tag, for example v1.0.0 or v1.0.0_2026-07-04.",
    )

    build_parser = subparsers.add_parser("build", help="Build and validate all artifacts.")
    build_parser.add_argument("--output-dir", type=Path, default=Path("dist"))

    smoke_parser = subparsers.add_parser(
        "smoke",
        help="Install built artifacts in a clean environment.",
    )
    smoke_parser.add_argument("--dist-dir", type=Path, default=Path("dist"))

    prepare_parser = subparsers.add_parser(
        "prepare",
        help="Validate, build, and smoke test a tagged release.",
    )
    prepare_parser.add_argument(
        "--tag",
        required=True,
        help="Git tag, for example v1.0.0 or v1.0.0_2026-07-04.",
    )
    prepare_parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    return parser


def _run_command(args: argparse.Namespace) -> None:
    if args.command == "check":
        check_release(root=_ROOT, tag=args.tag)
        return
    if args.command == "build":
        build_artifacts(root=_ROOT, output=args.output_dir)
        return
    if args.command == "smoke":
        smoke_test_artifacts(root=_ROOT, dist_dir=args.dist_dir)
        return
    if args.command == "prepare":
        check_release(root=_ROOT, tag=args.tag)
        output_dir = build_artifacts(root=_ROOT, output=args.output_dir)
        smoke_test_artifacts(root=_ROOT, dist_dir=output_dir)
        return
    raise ReleaseValidationError(f"Unsupported release command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _run_command(args)
    except (ReleaseValidationError, subprocess.CalledProcessError) as exc:
        print(f"release validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
