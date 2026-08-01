from __future__ import annotations as _annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.version import Version
from pydantic_acp import AdapterConfig, BlackBoxHarness
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from pydantic_ai.run import AgentRunResultEvent

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_PYPROJECT = REPO_ROOT / "packages" / "adapters" / "pydantic-acp" / "pyproject.toml"
ROOT_PYPROJECT = REPO_ROOT / "pyproject.toml"
MAKEFILE = REPO_ROOT / "Makefile"
TEST_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test.yml"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

SUPPORTED_FLOOR = Version("2.9.0")
SUPPORTED_CEILING = Version("2.22.0")
EXPECTED_MATRIX_VERSIONS = (
    "2.9.0",
    "2.9.1",
    "2.10.0",
    "2.11.0",
    "2.12.0",
    "2.13.0",
    "2.14.0",
    "2.14.1",
    "2.15.0",
    "2.16.0",
    "2.17.0",
    "2.18.0",
    "2.19.0",
    "2.20.0",
    "2.21.0",
    "2.22.0",
)
HARNESS_PIN = "pydantic-ai-harness[code-mode]==0.15.0"


def _requirement_named(dependencies: list[str], name: str) -> Requirement:
    for dependency in dependencies:
        requirement = Requirement(dependency)
        if requirement.name == name:
            return requirement
    raise AssertionError(f"expected dependency {name!r} in {dependencies}")


def _specifier_bounds(requirement: Requirement) -> tuple[Version, Version]:
    lower: Version | None = None
    upper: Version | None = None
    for specifier in requirement.specifier:
        version = Version(specifier.version)
        if specifier.operator in {">=", ">"}:
            lower = version
        elif specifier.operator in {"<=", "<"}:
            upper = version
    assert lower is not None, f"missing lower bound in {requirement}"
    assert upper is not None, f"missing upper bound in {requirement}"
    return lower, upper


def test_requirement_named_rejects_unknown_dependency() -> None:
    with pytest.raises(AssertionError, match=r"expected dependency 'missing'"):
        _requirement_named(["pydantic-ai-slim>=2.9.0"], "missing")


def _makefile_pydantic_ai_versions() -> list[str]:
    match = re.search(
        r"^PYDANTIC_AI_VERSIONS\s*:=\s*(.+)$",
        MAKEFILE.read_text(),
        flags=re.MULTILINE,
    )
    assert match is not None, "PYDANTIC_AI_VERSIONS missing from Makefile"
    return match.group(1).split()


def _workflow_pydantic_ai_versions() -> list[str]:
    data: dict[str, Any] = yaml.safe_load(TEST_WORKFLOW.read_text())
    matrix = data["jobs"]["pydantic-ai-compatibility"]["strategy"]["matrix"]
    versions = matrix["pydantic-ai-version"]
    assert isinstance(versions, list)
    return [str(version) for version in versions]


def test_pydantic_acp_declares_supported_pydantic_ai_range() -> None:
    data: dict[str, Any] = tomllib.loads(PACKAGE_PYPROJECT.read_text())
    dependencies: list[str] = data["project"]["dependencies"]
    requirement = _requirement_named(dependencies, "pydantic-ai-slim")
    lower, upper = _specifier_bounds(requirement)

    assert lower == SUPPORTED_FLOOR
    assert upper == SUPPORTED_CEILING
    assert requirement.specifier.contains("2.9.0")
    assert requirement.specifier.contains(str(SUPPORTED_CEILING))
    assert not requirement.specifier.contains("2.8.0")
    assert not requirement.specifier.contains("2.23.0")


def test_root_dev_extra_pins_supported_pydantic_ai_and_harness() -> None:
    data: dict[str, Any] = tomllib.loads(ROOT_PYPROJECT.read_text())
    dev_dependencies: list[str] = data["project"]["optional-dependencies"]["dev"]
    slim = _requirement_named(dev_dependencies, "pydantic-ai-slim")
    lower, upper = _specifier_bounds(slim)

    assert lower == SUPPORTED_FLOOR
    assert upper == SUPPORTED_CEILING
    assert HARNESS_PIN in dev_dependencies


def test_pydantic_acp_harness_extra_requires_the_tested_harness_line() -> None:
    data: dict[str, Any] = tomllib.loads(PACKAGE_PYPROJECT.read_text())
    harness_dependencies: list[str] = data["project"]["optional-dependencies"]["harness"]
    harness = _requirement_named(harness_dependencies, "pydantic-ai-harness")

    assert str(harness.specifier) == "==0.15.0"


def test_pydantic_ai_matrix_matches_package_support_bounds() -> None:
    makefile_versions = _makefile_pydantic_ai_versions()
    workflow_versions = _workflow_pydantic_ai_versions()

    assert makefile_versions == list(EXPECTED_MATRIX_VERSIONS)
    assert workflow_versions == list(EXPECTED_MATRIX_VERSIONS)
    assert makefile_versions == workflow_versions

    parsed = [Version(version) for version in makefile_versions]
    assert parsed[0] == SUPPORTED_FLOOR
    assert parsed[-1] == SUPPORTED_CEILING
    assert all(version >= SUPPORTED_FLOOR for version in parsed)
    assert all(version <= SUPPORTED_CEILING for version in parsed)
    assert not any(version < Version("2.9.0") for version in parsed)


def test_pydantic_ai_matrix_covers_each_supported_minor_endpoint() -> None:
    matrix_versions = {Version(version) for version in _makefile_pydantic_ai_versions()}
    expected_minors = {
        Version("2.9.0"),
        Version("2.9.1"),
        Version("2.10.0"),
        Version("2.11.0"),
        Version("2.12.0"),
        Version("2.13.0"),
        Version("2.14.0"),
        Version("2.14.1"),
        Version("2.15.0"),
        Version("2.16.0"),
        Version("2.17.0"),
        Version("2.18.0"),
        Version("2.19.0"),
        Version("2.20.0"),
        Version("2.21.0"),
        Version("2.22.0"),
    }
    assert matrix_versions == expected_minors


def test_ci_workflows_use_setup_python_v7_when_the_action_is_present() -> None:
    setup_python_versions: set[str] = set()
    for workflow in WORKFLOWS_DIR.glob("*.yml"):
        setup_python_versions.update(
            re.findall(r"actions/setup-python@(v\d+)", workflow.read_text()),
        )

    assert setup_python_versions == {"v7"}


async def test_pydantic_ai_agent_stream_lifecycle_is_compatible() -> None:
    agent: Agent[None, str] = Agent(
        TestModel(custom_output_text="compat-ok"),
        deps_type=type(None),
        name="compat-agent",
        output_type=str,
    )

    async with agent.run_stream_events("ping") as stream:
        events = [event async for event in stream]

    result_events = [event for event in events if isinstance(event, AgentRunResultEvent)]
    assert result_events
    assert result_events[-1].result.output == "compat-ok"


async def test_pydantic_acp_adapter_accepts_supported_pydantic_ai_agent(
    tmp_path: Path,
) -> None:
    agent: Agent[None, str] = Agent(
        TestModel(custom_output_text="adapter-ok"),
        deps_type=type(None),
        name="compat-adapter-agent",
        output_type=str,
    )
    harness = BlackBoxHarness.create(
        agent=agent,
        config=AdapterConfig(),
    )

    session = await harness.new_session(cwd=str(tmp_path))
    response = await harness.prompt_text("hello", session_id=session.session_id)

    assert response.stop_reason == "end_turn"
    assert harness.agent_messages(session_id=session.session_id) == ["adapter-ok"]


def test_installed_pydantic_ai_is_inside_declared_support_range() -> None:
    import pydantic_ai

    installed = Version(pydantic_ai.__version__)
    assert SUPPORTED_FLOOR <= installed <= SUPPORTED_CEILING
