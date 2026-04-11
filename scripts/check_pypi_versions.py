from __future__ import annotations as _annotations

import argparse
import ast
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from packaging.version import InvalidVersion, Version

PYPI_URL_TEMPLATE = "https://pypi.org/pypi/{package}/json"


@dataclass(frozen=True, slots=True)
class PackageVersionTarget:
    package_name: str
    version_file: Path


@dataclass(frozen=True, slots=True)
class PublishedVersionOverride:
    package_name: str
    version: Version


PACKAGE_TARGETS = (
    PackageVersionTarget("acpkit", Path("src/acpkit/_version.py")),
    PackageVersionTarget(
        "pydantic-acp",
        Path("packages/adapters/pydantic-acp/src/pydantic_acp/_version.py"),
    ),
    PackageVersionTarget(
        "codex-auth-helper",
        Path("packages/helpers/codex-auth-helper/src/codex_auth_helper/_version.py"),
    ),
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ensure local package versions are strictly greater than the latest PyPI releases.",
    )
    parser.add_argument(
        "--published-version",
        action="append",
        default=[],
        metavar="PACKAGE=VERSION",
        help="Override the published version for a package. Useful for local validation without network access.",
    )
    return parser.parse_args()


def _read_version(version_file: Path) -> Version:
    module = ast.parse(version_file.read_text(encoding="utf-8"), filename=str(version_file))
    for statement in module.body:
        if not isinstance(statement, ast.Assign):
            continue
        if len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        if not isinstance(target, ast.Name) or target.id != "__version__":
            continue
        value = statement.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            msg = f"{version_file} must assign a string literal to __version__"
            raise SystemExit(msg)
        try:
            return Version(value.value)
        except InvalidVersion as exc:
            msg = f"{version_file} contains an invalid version: {value.value!r}"
            raise SystemExit(msg) from exc
    msg = f"{version_file} does not define __version__"
    raise SystemExit(msg)


def _parse_overrides(raw_overrides: list[str]) -> dict[str, PublishedVersionOverride]:
    overrides: dict[str, PublishedVersionOverride] = {}
    for raw_override in raw_overrides:
        package_name, separator, version_text = raw_override.partition("=")
        if not separator:
            msg = f"Invalid --published-version value: {raw_override!r}"
            raise SystemExit(msg)
        normalized_package_name = package_name.strip()
        normalized_version_text = version_text.strip()
        if not normalized_package_name or not normalized_version_text:
            msg = f"Invalid --published-version value: {raw_override!r}"
            raise SystemExit(msg)
        try:
            overrides[normalized_package_name] = PublishedVersionOverride(
                package_name=normalized_package_name,
                version=Version(normalized_version_text),
            )
        except InvalidVersion as exc:
            msg = (
                f"Invalid published override for {normalized_package_name!r}: "
                f"{normalized_version_text!r}"
            )
            raise SystemExit(msg) from exc
    return overrides


def _fetch_pypi_version(package_name: str) -> Version:
    url = PYPI_URL_TEMPLATE.format(package=package_name)
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        msg = f"Failed to fetch PyPI metadata for {package_name!r}: HTTP {exc.code}"
        raise SystemExit(msg) from exc
    except urllib.error.URLError as exc:
        msg = f"Failed to reach PyPI for {package_name!r}: {exc.reason}"
        raise SystemExit(msg) from exc

    info = payload.get("info")
    if not isinstance(info, dict):
        msg = f"PyPI metadata for {package_name!r} is missing the info object"
        raise SystemExit(msg)
    version_text = info.get("version")
    if not isinstance(version_text, str):
        msg = f"PyPI metadata for {package_name!r} is missing the published version"
        raise SystemExit(msg)
    try:
        return Version(version_text)
    except InvalidVersion as exc:
        msg = f"PyPI returned an invalid version for {package_name!r}: {version_text!r}"
        raise SystemExit(msg) from exc


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    overrides = _parse_overrides(args.published_version)
    failed = False

    for target in PACKAGE_TARGETS:
        local_version = _read_version(repo_root / target.version_file)
        published_version = (
            overrides[target.package_name].version
            if target.package_name in overrides
            else _fetch_pypi_version(target.package_name)
        )
        if local_version <= published_version:
            failed = True
            print(
                f"{target.package_name}: local version {local_version} must be greater than "
                f"published version {published_version}",
            )
            continue
        print(
            f"{target.package_name}: ok (local {local_version} > published {published_version})",
        )

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
