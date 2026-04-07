from __future__ import annotations as _annotations

import importlib.util
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from pydantic_ai import Agent

from acpkit import (
    AcpKitError,
    MissingAdapterError,
    launch_command,
    launch_target,
    load_target,
    run_target,
)
from acpkit.cli import cli, main


def _write_module(tmp_path: Path, module_name: str, source: str) -> None:
    (tmp_path / f"{module_name}.py").write_text(source, encoding="utf-8")


def _patch_adapter_modules(
    monkeypatch: pytest.MonkeyPatch,
    *,
    available_modules: set[str],
) -> None:
    def fake_find_spec(name: str, package: str | None = None) -> ModuleSpec | None:
        if name in {"pydantic_acp", "pydantic_ai"}:
            if name in available_modules:
                return ModuleSpec(name, loader=None)
            return None
        return importlib.util.find_spec(name, package)

    monkeypatch.setattr("acpkit.adapters.find_spec", fake_find_spec)


def test_load_target_resolves_module_attribute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_module(
        tmp_path,
        "sample_loader_app",
        "\n".join(
            (
                "from pydantic_ai import Agent",
                "from pydantic_ai.models.test import TestModel",
                "",
                'agent = Agent(TestModel(custom_output_text="loaded"))',
            )
        ),
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    loaded_target = load_target("sample_loader_app:agent")

    assert isinstance(loaded_target, Agent)


def test_load_target_uses_current_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_module(
        tmp_path,
        "sample_cwd_app",
        "\n".join(
            (
                "from pydantic_ai import Agent",
                "from pydantic_ai.models.test import TestModel",
                "",
                'agent = Agent(TestModel(custom_output_text="cwd"))',
            )
        ),
    )
    monkeypatch.chdir(tmp_path)

    loaded_target = load_target("sample_cwd_app:agent")

    assert isinstance(loaded_target, Agent)


def test_load_target_resolves_latest_agent_when_attribute_is_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_module(
        tmp_path,
        "sample_latest_agent_app",
        "\n".join(
            (
                "from pydantic_ai import Agent",
                "from pydantic_ai.models.test import TestModel",
                "",
                'first_agent = Agent(TestModel(custom_output_text="first"))',
                'second_agent = Agent(TestModel(custom_output_text="second"))',
            )
        ),
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    loaded_target = load_target("sample_latest_agent_app")

    assert isinstance(loaded_target, Agent)
    assert loaded_target is load_target("sample_latest_agent_app:second_agent")


def test_load_target_uses_explicit_import_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_home = tmp_path / "agent_home"
    agent_home.mkdir()
    _write_module(
        agent_home,
        "sample_external_app",
        "\n".join(
            (
                "from pydantic_ai import Agent",
                "from pydantic_ai.models.test import TestModel",
                "",
                'agent = Agent(TestModel(custom_output_text="external"))',
            )
        ),
    )
    monkeypatch.chdir(tmp_path)

    loaded_target = load_target("sample_external_app:agent", import_roots=[str(agent_home)])

    assert isinstance(loaded_target, Agent)


def test_run_target_dispatches_to_pydantic_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_module(
        tmp_path,
        "sample_run_app",
        "\n".join(
            (
                "from pydantic_ai import Agent",
                "from pydantic_ai.models.test import TestModel",
                "",
                'agent = Agent(TestModel(custom_output_text="run"))',
            )
        ),
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    captured_agents: list[Agent[Any, Any]] = []

    def record_run(agent: Agent[Any, Any]) -> None:
        captured_agents.append(agent)

    run_target("sample_run_app:agent", pydantic_runner=record_run)

    assert len(captured_agents) == 1


def test_run_target_reports_missing_pydantic_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_module(
        tmp_path,
        "sample_missing_pydantic_adapter",
        "\n".join(
            (
                "from pydantic_ai import Agent",
                "from pydantic_ai.models.test import TestModel",
                "",
                'agent = Agent(TestModel(custom_output_text="missing"))',
            )
        ),
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    _patch_adapter_modules(monkeypatch, available_modules={"pydantic_ai"})

    with pytest.raises(MissingAdapterError) as exc_info:
        run_target("sample_missing_pydantic_adapter:agent")

    assert 'uv pip install "acpkit[pydantic]"' in str(exc_info.value)


def test_load_target_reports_missing_pydantic_adapter_from_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_import_module(name: str, package: str | None = None) -> object:
        del package
        if name == "sample_missing_dependency":
            raise ModuleNotFoundError(
                "No module named 'pydantic_ai'",
                name="pydantic_ai",
            )
        return importlib.util.module_from_spec(ModuleSpec(name, loader=None))

    _patch_adapter_modules(monkeypatch, available_modules=set())
    monkeypatch.setattr("acpkit.runtime.importlib.import_module", fake_import_module)

    with pytest.raises(MissingAdapterError) as exc_info:
        load_target("sample_missing_dependency:agent")

    assert 'uv pip install "acpkit[pydantic]"' in str(exc_info.value)


def test_run_target_reports_when_no_adapters_are_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_module(
        tmp_path,
        "sample_no_adapters",
        "value = object()\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    _patch_adapter_modules(monkeypatch, available_modules=set())

    with pytest.raises(MissingAdapterError) as exc_info:
        run_target("sample_no_adapters:value")

    assert "No ACP adapters are installed." in str(exc_info.value)
    assert 'uv pip install "acpkit[pydantic]"' in str(exc_info.value)


def test_launch_target_invokes_toad_with_mirrored_run_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_commands: list[list[str]] = []

    class CompletedProcess:
        returncode = 0

    def record_run(command: list[str], *, check: bool) -> CompletedProcess:
        assert check is False
        captured_commands.append(command)
        return CompletedProcess()

    monkeypatch.setattr("acpkit.runtime.subprocess.run", record_run)

    exit_code = launch_target(
        "sample_launch_app:agent", import_roots=("/tmp/one", "/tmp/two words")
    )

    assert exit_code == 0
    assert captured_commands == [
        [
            "uvx",
            "--python",
            "3.14",
            "--from",
            "batrachian-toad",
            "toad",
            "acp",
            "acpkit run sample_launch_app:agent -p /tmp/one -p '/tmp/two words'",
        ]
    ]


def test_launch_target_reports_missing_uvx_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_missing_uvx(command: list[str], *, check: bool) -> object:
        del command, check
        raise FileNotFoundError("uvx")

    monkeypatch.setattr("acpkit.runtime.subprocess.run", raise_missing_uvx)

    with pytest.raises(AcpKitError) as exc_info:
        launch_target("sample_launch_app:agent")

    assert '`uv pip install "acpkit[launch]"`' in str(exc_info.value)


def test_launch_command_invokes_toad_with_raw_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_commands: list[list[str]] = []

    class CompletedProcess:
        returncode = 0

    def record_run(command: list[str], *, check: bool) -> CompletedProcess:
        assert check is False
        captured_commands.append(command)
        return CompletedProcess()

    monkeypatch.setattr("acpkit.runtime.subprocess.run", record_run)

    exit_code = launch_command("python3.11 strong_agent.py")

    assert exit_code == 0
    assert captured_commands == [
        [
            "uvx",
            "--python",
            "3.14",
            "--from",
            "batrachian-toad",
            "toad",
            "acp",
            "python3.11 strong_agent.py",
        ]
    ]


def test_cli_run_command_invokes_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured_targets: list[tuple[str, tuple[str, ...]]] = []

    def record_run_target(target: str, *, import_roots: tuple[str, ...] | None = None) -> None:
        captured_targets.append((target, () if import_roots is None else import_roots))

    monkeypatch.setattr("acpkit.cli.run_target", record_run_target)

    result = CliRunner().invoke(
        cli,
        ["run", "sample_main_app:agent", "-p", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert captured_targets == [("sample_main_app:agent", (str(tmp_path),))]


def test_cli_launch_command_invokes_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_targets: list[tuple[str, tuple[str, ...]]] = []

    def record_launch_target(target: str, *, import_roots: tuple[str, ...] | None = None) -> int:
        captured_targets.append((target, () if import_roots is None else import_roots))
        return 0

    monkeypatch.setattr("acpkit.cli.launch_target", record_launch_target)

    result = CliRunner().invoke(
        cli,
        ["launch", "sample_main_app:agent", "-p", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert captured_targets == [("sample_main_app:agent", (str(tmp_path),))]


def test_cli_launch_command_accepts_raw_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_commands: list[str] = []

    def record_launch_command(command: str) -> int:
        captured_commands.append(command)
        return 0

    monkeypatch.setattr("acpkit.cli.launch_raw_command", record_launch_command)

    result = CliRunner().invoke(
        cli,
        ["launch", "-c", "python3.11 strong_agent.py"],
    )

    assert result.exit_code == 0
    assert captured_commands == ["python3.11 strong_agent.py"]


def test_cli_reports_missing_adapter_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_missing_adapter(
        target: str,
        *,
        import_roots: tuple[str, ...] | None = None,
    ) -> None:
        del target, import_roots
        raise MissingAdapterError.for_any_adapter()

    monkeypatch.setattr("acpkit.cli.run_target", raise_missing_adapter)

    result = CliRunner().invoke(cli, ["run", "demo:agent"])

    assert result.exit_code == 2
    assert "No ACP adapters are installed." in result.output
    assert 'uv pip install "acpkit[pydantic]"' in result.output


def test_cli_launch_command_returns_subprocess_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def record_launch_target(
        target: str,
        *,
        import_roots: tuple[str, ...] | None = None,
    ) -> int:
        del target, import_roots
        return 7

    monkeypatch.setattr("acpkit.cli.launch_target", record_launch_target)

    result = CliRunner().invoke(cli, ["launch", "demo:agent"])

    assert result.exit_code == 7


def test_cli_launch_command_requires_exactly_one_mode() -> None:
    result = CliRunner().invoke(cli, ["launch"])

    assert result.exit_code == 2
    assert "Provide exactly one of `TARGET` or `--command`." in result.output


def test_cli_launch_command_rejects_path_with_raw_command() -> None:
    result = CliRunner().invoke(
        cli,
        ["launch", "-c", "python3.11 strong_agent.py", "-p", "/tmp/demo"],
    )

    assert result.exit_code == 2
    assert "`--path` can only be used when launching a target." in result.output


def test_main_returns_nonzero_for_click_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def raise_missing_adapter(
        target: str,
        *,
        import_roots: tuple[str, ...] | None = None,
    ) -> None:
        del target, import_roots
        raise MissingAdapterError.for_any_adapter()

    monkeypatch.setattr("acpkit.cli.run_target", raise_missing_adapter)

    exit_code = main(["run", "demo:agent"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "No ACP adapters are installed." in captured.err
