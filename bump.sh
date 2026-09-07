#!/usr/bin/env bash

set -euo pipefail

python_version_files=(
  "src/acpkit/_version.py"
  "packages/adapters/langchain-acp/src/langchain_acp/_version.py"
  "packages/adapters/pydantic-acp/src/pydantic_acp/_version.py"
  "packages/helpers/codex-auth-helper/src/codex_auth_helper/_version.py"
  "packages/transports/acpremote/src/acpremote/_version.py"
)

plain_version_files=(
  "VERSION"
  "packages/adapters/langchain-acp/VERSION"
  "packages/adapters/pydantic-acp/VERSION"
  "packages/helpers/codex-auth-helper/VERSION"
  "packages/transports/acpremote/VERSION"
)

root_project_file="pyproject.toml"

all_version_files=(
  "${python_version_files[@]}"
  "${plain_version_files[@]}"
  "${root_project_file}"
)

usage() {
  cat >&2 <<'EOF'
Usage:
  ./bump.sh <version>
  ./bump.sh -c -m "commit message"
EOF
}

commit_after_bump='false'
commit_message=''
target_version=''

while getopts ":cm:" option; do
  case "${option}" in
    c)
      commit_after_bump='true'
      ;;
    m)
      commit_message="${OPTARG}"
      ;;
    :)
      echo "Missing value for -${OPTARG}" >&2
      usage
      exit 1
      ;;
    \?)
      echo "Unknown option: -${OPTARG}" >&2
      usage
      exit 1
      ;;
  esac
done

shift $((OPTIND - 1))

if [[ "${commit_after_bump}" == 'true' ]]; then
  if [[ $# -ne 0 ]]; then
    usage
    exit 1
  fi
  if [[ -z "${commit_message}" ]]; then
    echo "The -m option is required when using -c." >&2
    usage
    exit 1
  fi
else
  if [[ -n "${commit_message}" ]]; then
    echo "The -m option can only be used together with -c." >&2
    usage
    exit 1
  fi
  if [[ $# -ne 1 ]]; then
    usage
    exit 1
  fi
  target_version="$1"
fi

version_output="$(
  python3.11 - "${commit_after_bump}" "${target_version}" \
    "${#python_version_files[@]}" \
    "${python_version_files[@]}" \
    "${plain_version_files[@]}" \
    "${root_project_file}" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

commit_after_bump = sys.argv[1] == 'true'
target_version = sys.argv[2]
python_path_count = int(sys.argv[3])
python_paths = [Path(path_str) for path_str in sys.argv[4 : 4 + python_path_count]]
plain_and_project_paths = [Path(path_str) for path_str in sys.argv[4 + python_path_count :]]
plain_paths = plain_and_project_paths[:-1]
root_project_path = plain_and_project_paths[-1]

python_pattern = re.compile(
    r"^__version__ = (?P<quote>['\"])(?P<version>[^'\"]+)(?P=quote)$",
    re.MULTILINE,
)
supported_version_pattern = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:(?:a|b|rc)\d+)?$",
)


def release_specifier(version: str) -> str:
    match = supported_version_pattern.fullmatch(version)
    if match is None:
        raise SystemExit(
            f"Unsupported release version {version!r}; expected X.Y.Z or X.Y.ZrcN."
        )
    if re.search(r"(?:a|b|rc)\d+$", version) is not None:
        return f"=={version}"
    next_major = int(match.group("major")) + 1
    return f">={version},<{next_major}.0.0"


def update_root_optional_dependencies(path: Path, version: str) -> None:
    specifier = release_specifier(version)
    replacements = {
        "codex": f'codex = ["codex-auth-helper{specifier}"]',
        "deepagents": f'deepagents = ["langchain-acp[deepagents]{specifier}"]',
        "langchain": f'langchain = ["langchain-acp{specifier}"]',
        "pydantic": f'pydantic = ["pydantic-acp{specifier}"]',
        "remote": f'remote = ["acpremote{specifier}"]',
    }
    content = path.read_text(encoding="utf-8")
    for extra, replacement in replacements.items():
        pattern = re.compile(rf"^{re.escape(extra)}\s*=\s*\[[^\n]*\]$", re.MULTILINE)
        content, count = pattern.subn(replacement, content, count=1)
        if count != 1:
            raise SystemExit(f"Expected exactly one root optional dependency line for {extra}.")
    path.write_text(content, encoding="utf-8")
    print(f"updated {path}")

all_versions: list[str] = []

for path in python_paths:
    content = path.read_text(encoding="utf-8")
    match = python_pattern.search(content)
    if match is None:
        msg = f"Expected exactly one __version__ assignment in {path}"
        raise SystemExit(msg)
    all_versions.append(match.group("version"))

for path in plain_paths:
    version = path.read_text(encoding="utf-8").strip()
    if not version:
        raise SystemExit(f"Expected non-empty version text in {path}")
    all_versions.append(version)

all_paths = [*python_paths, *plain_paths]
unique_versions = set(all_versions)
current_version = all_versions[0]
if len(unique_versions) > 1:
    details = ", ".join(
        f"{path}={version}" for path, version in zip(all_paths, all_versions, strict=True)
    )
    non_target_versions = unique_versions - {target_version}
    partial_target_bump = (
        not commit_after_bump
        and target_version in unique_versions
        and len(non_target_versions) == 1
    )
    if not partial_target_bump:
        raise SystemExit(f"Version files are out of sync: {details}")
    current_version = non_target_versions.pop()

if commit_after_bump:
    parts = current_version.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise SystemExit(
            "Automatic commit bump requires a semantic version in the form major.minor.patch."
        )
    major, minor, _patch = (int(part) for part in parts)
    new_version = f"{major}.{minor + 1}.0"
else:
    new_version = target_version

for path in python_paths:
    content = path.read_text(encoding="utf-8")
    updated_content, replacements = python_pattern.subn(
        lambda match: f"__version__ = {match.group('quote')}{new_version}{match.group('quote')}",
        content,
        count=1,
    )
    if replacements != 1:
        msg = f"Expected exactly one __version__ assignment in {path}"
        raise SystemExit(msg)
    path.write_text(updated_content, encoding="utf-8")
    print(f"updated {path}")

for path in plain_paths:
    path.write_text(f"{new_version}\n", encoding="utf-8")
    print(f"updated {path}")

update_root_optional_dependencies(root_project_path, new_version)

print(f"current_version={current_version}")
print(f"new_version={new_version}")
if commit_after_bump:
    new_major, new_minor, _new_patch = (int(part) for part in new_version.split("."))
    old_version_for_commit = f"{new_major}.{new_minor - 1}.0"
else:
    old_version_for_commit = current_version
print(f"old_version_for_commit={old_version_for_commit}")
PY
)"

current_version=''
new_version=''
old_version_for_commit=''

while IFS= read -r line; do
  case "${line}" in
    current_version=*)
      current_version="${line#current_version=}"
      ;;
    new_version=*)
      new_version="${line#new_version=}"
      ;;
    old_version_for_commit=*)
      old_version_for_commit="${line#old_version_for_commit=}"
      ;;
    *)
      echo "${line}"
      ;;
  esac
done <<<"${version_output}"

echo "Version updated: ${current_version} -> ${new_version}"

if [[ "${commit_after_bump}" == 'true' ]]; then
  git add "${all_version_files[@]}"
  git commit \
    -m "bump version ${old_version_for_commit} -> ${new_version}" \
    -m "${commit_message}"
fi
