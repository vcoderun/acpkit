from __future__ import annotations as _annotations

import importlib
import importlib.util
import sys
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, NoReturn, cast

import pytest
from click.testing import CliRunner
from pydantic_ai import Agent

from acpkit import (
    AcpKitError,
    MissingAdapterError,
    connect_acp,
    launch_command,
    launch_target,
    load_target,
    run_remote_addr,
    run_target,
    serve_acp,
    serve_target,
)
from acpkit.adapters import (
    _run_pydantic_target,
    find_adapter_by_module_name,
    find_matching_adapter,
    is_acp_target,
)
from acpkit.cli import cli, main
from acpkit.runtime import (
    UnsupportedAgentError,
    _missing_adapter_from_import_error,
    parse_target_ref,
)


def _write_module(tmp_path: Path, module_name: str, source: str) -> None:
    (tmp_path / f"{module_name}.py").write_text(source, encoding="utf-8")


def _patch_adapter_modules(
    monkeypatch: pytest.MonkeyPatch,
    *,
    available_modules: set[str],
) -> None:
    def fake_find_spec(name: str, package: str | None = None) -> ModuleSpec | None:
        if name in {
            "acp",
            "acpremote",
            "langchain",
            "langchain_acp",
            "langgraph",
            "pydantic_acp",
            "pydantic_ai",
        }:
            if name in available_modules:
                return ModuleSpec(name, loader=None)
            return None
        return importlib.util.find_spec(name, package)

    monkeypatch.setattr("acpkit.adapters.find_spec", fake_find_spec)


class _NativeAcpAgent:
    async def initialize(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return None

    async def new_session(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return None

    async def load_session(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return None

    async def list_sessions(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return None

    async def set_session_mode(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return None

    async def set_session_model(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return None

    async def set_config_option(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return None

    async def authenticate(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return None

    async def prompt(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return None

    async def fork_session(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return None

    async def resume_session(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return None

    async def close_session(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return None

    async def cancel(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return None

    async def ext_method(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return {}

    async def ext_notification(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    def on_connect(self, conn: Any) -> None:
        del conn


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


def test_parse_target_ref_rejects_empty_module_and_attribute() -> None:
    with pytest.raises(AcpKitError, match="module name"):
        parse_target_ref(":agent")

    with pytest.raises(AcpKitError, match="attribute cannot be empty"):
        parse_target_ref("demo:")


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
    def fake_import_module(name: str, package: str | None = None) -> ModuleType:
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
        "value = 1\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    _patch_adapter_modules(monkeypatch, available_modules=set())

    with pytest.raises(MissingAdapterError) as exc_info:
        run_target("sample_no_adapters:value")

    assert "No ACP adapters are installed." in str(exc_info.value)
    assert 'uv pip install "acpkit[pydantic]"' in str(exc_info.value)


def test_run_target_reports_unsupported_value_when_adapters_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_module(tmp_path, "sample_unsupported_value", "value = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    _patch_adapter_modules(monkeypatch, available_modules={"pydantic_ai", "pydantic_acp"})

    with pytest.raises(UnsupportedAgentError, match="No installed adapter supports"):
        run_target("sample_unsupported_value:value")


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
    def raise_missing_uvx(command: list[str], *, check: bool) -> NoReturn:
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

    exit_code = launch_command("python3.11 finance_agent.py")

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
            "python3.11 finance_agent.py",
        ]
    ]


def test_launch_command_rejects_empty_command() -> None:
    with pytest.raises(AcpKitError, match="cannot be empty"):
        launch_command("   ")


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


def test_cli_run_command_invokes_remote_runtime_for_addr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, str | None]] = []

    def record_run_remote_addr(addr: str, *, token_env: str | None = None) -> None:
        captured.append((addr, token_env))

    monkeypatch.setattr("acpkit.cli.run_remote_addr", record_run_remote_addr)

    result = CliRunner().invoke(
        cli,
        ["run", "--addr", "ws://agents.example.com/acp/ws", "--token-env", "ACP_TOKEN"],
    )

    assert result.exit_code == 0
    assert captured == [("ws://agents.example.com/acp/ws", "ACP_TOKEN")]


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


def test_cli_serve_command_invokes_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    def record_serve_target(
        target: str,
        *,
        import_roots: tuple[str, ...] | None = None,
        host: str = "127.0.0.1",
        port: int = 8080,
        mount_path: str = "/acp",
        token_env: str | None = None,
    ) -> None:
        captured.append(
            {
                "target": target,
                "import_roots": () if import_roots is None else import_roots,
                "host": host,
                "port": port,
                "mount_path": mount_path,
                "token_env": token_env,
            }
        )

    monkeypatch.setattr("acpkit.cli.serve_target", record_serve_target)

    result = CliRunner().invoke(
        cli,
        [
            "serve",
            "sample_main_app:agent",
            "-p",
            str(tmp_path),
            "--host",
            "0.0.0.0",
            "--port",
            "9000",
            "--mount-path",
            "/remote",
            "--token-env",
            "ACP_TOKEN",
        ],
    )

    assert result.exit_code == 0
    assert captured == [
        {
            "target": "sample_main_app:agent",
            "import_roots": (str(tmp_path),),
            "host": "0.0.0.0",
            "port": 9000,
            "mount_path": "/remote",
            "token_env": "ACP_TOKEN",
        }
    ]


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
        ["launch", "-c", "python3.11 finance_agent.py"],
    )

    assert result.exit_code == 0
    assert captured_commands == ["python3.11 finance_agent.py"]


def test_root_adapter_detects_native_acp_targets() -> None:
    agent = _NativeAcpAgent()

    adapter = find_matching_adapter(agent)

    assert is_acp_target(agent)
    assert adapter is not None
    assert adapter.adapter_id == "acp"


def test_run_target_routes_pydantic_agent_through_adapter_entrypoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_module(
        tmp_path,
        "sample_runtime_app",
        "\n".join(
            (
                "from pydantic_ai import Agent",
                "from pydantic_ai.models.test import TestModel",
                "",
                'agent = Agent(TestModel(custom_output_text="runtime"))',
            )
        ),
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("sample_runtime_app", None)

    import pydantic_acp

    captured_names: list[str | None] = []

    def record_run_acp(agent: Agent[Any, Any]) -> None:
        captured_names.append(agent.name)

    monkeypatch.setattr(pydantic_acp, "run_acp", record_run_acp)

    run_target("sample_runtime_app:agent", import_roots=(str(tmp_path),))

    assert captured_names == [None]


def test_run_target_dispatches_native_acp_target_through_acp_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_module(
        tmp_path,
        "sample_native_acp_app",
        "\n".join(
            (
                "class DemoAgent:",
                "    async def initialize(self, *args, **kwargs):",
                "        return None",
                "    async def new_session(self, *args, **kwargs):",
                "        return None",
                "    async def load_session(self, *args, **kwargs):",
                "        return None",
                "    async def list_sessions(self, *args, **kwargs):",
                "        return None",
                "    async def set_session_mode(self, *args, **kwargs):",
                "        return None",
                "    async def set_session_model(self, *args, **kwargs):",
                "        return None",
                "    async def set_config_option(self, *args, **kwargs):",
                "        return None",
                "    async def authenticate(self, *args, **kwargs):",
                "        return None",
                "    async def prompt(self, *args, **kwargs):",
                "        return None",
                "    async def fork_session(self, *args, **kwargs):",
                "        return None",
                "    async def resume_session(self, *args, **kwargs):",
                "        return None",
                "    async def close_session(self, *args, **kwargs):",
                "        return None",
                "    async def cancel(self, *args, **kwargs):",
                "        return None",
                "    async def ext_method(self, *args, **kwargs):",
                "        return {}",
                "    async def ext_notification(self, *args, **kwargs):",
                "        return None",
                "    def on_connect(self, conn):",
                "        return None",
                "",
                "agent = DemoAgent()",
            )
        ),
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    captured_agents: list[Any] = []

    run_target(
        "sample_native_acp_app:agent",
        import_roots=(str(tmp_path),),
        acp_runner=lambda agent: captured_agents.append(agent),
    )

    assert len(captured_agents) == 1


def test_run_target_rejects_non_pydantic_value_for_pydantic_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_module(tmp_path, "sample_runner_mismatch", "value = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    _patch_adapter_modules(monkeypatch, available_modules={"pydantic_ai", "pydantic_acp"})

    from acpkit.adapters import _ADAPTER_DEFINITIONS

    pydantic_adapter = next(
        adapter for adapter in _ADAPTER_DEFINITIONS if adapter.adapter_id == "pydantic"
    )
    monkeypatch.setattr("acpkit.runtime.find_matching_adapter", lambda target: pydantic_adapter)

    with pytest.raises(UnsupportedAgentError, match="Expected a `pydantic_ai.Agent` instance."):
        run_target("sample_runner_mismatch:value", pydantic_runner=lambda agent: None)


def test_run_remote_addr_proxies_remote_agent_through_acp_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel_agent = object()
    observed: dict[str, Any] = {}
    original_import_module = importlib.import_module

    async def fake_run_agent(agent: Any) -> None:
        observed["run_agent"] = agent

    def fake_connect_acp(addr: str, **kwargs: Any) -> object:
        observed["connect_acp"] = (addr, kwargs)
        return sentinel_agent

    def fake_import_module(name: str, package: str | None = None) -> ModuleType:
        if name == "acpremote":
            return cast(ModuleType, SimpleNamespace(connect_acp=fake_connect_acp))
        if name == "acp":
            return cast(ModuleType, SimpleNamespace(run_agent=fake_run_agent))
        return original_import_module(name, package)

    monkeypatch.setattr("acpkit.runtime.importlib.import_module", fake_import_module)
    monkeypatch.setenv("ACP_REMOTE_TOKEN", "secret")

    run_remote_addr("ws://agents.example.com/acp/ws", token_env="ACP_REMOTE_TOKEN")

    assert observed["connect_acp"] == (
        "ws://agents.example.com/acp/ws",
        {"bearer_token": "secret"},
    )
    assert observed["run_agent"] is sentinel_agent


def test_serve_target_materializes_adapter_backed_agent_and_runs_remote_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_module(
        tmp_path,
        "sample_serve_app",
        "\n".join(
            (
                "from pydantic_ai import Agent",
                "from pydantic_ai.models.test import TestModel",
                "",
                "agent = Agent(TestModel(custom_output_text='serve'))",
            )
        ),
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    observed: dict[str, Any] = {}
    original_import_module = importlib.import_module

    class _FakeServer:
        async def serve_forever(self) -> None:
            observed["serve_forever"] = True

        def close(self) -> None:
            observed["closed"] = True

        async def wait_closed(self) -> None:
            observed["wait_closed"] = True

    def fake_create_acp_agent(agent: Any) -> str:
        observed["acp_agent"] = agent
        return "materialized-agent"

    async def fake_serve_acp(agent: Any, **kwargs: Any) -> _FakeServer:
        observed["serve_acp"] = (agent, kwargs)
        return _FakeServer()

    def fake_import_module(name: str, package: str | None = None) -> ModuleType:
        if name == "pydantic_acp":
            return cast(ModuleType, SimpleNamespace(create_acp_agent=fake_create_acp_agent))
        if name == "acpremote":
            return cast(ModuleType, SimpleNamespace(serve_acp=fake_serve_acp))
        return original_import_module(name, package)

    monkeypatch.setattr("acpkit.runtime.importlib.import_module", fake_import_module)
    monkeypatch.setenv("ACP_REMOTE_TOKEN", "secret")

    serve_target(
        "sample_serve_app:agent",
        import_roots=(str(tmp_path),),
        host="0.0.0.0",
        port=9000,
        mount_path="/remote",
        token_env="ACP_REMOTE_TOKEN",
    )

    assert observed["serve_acp"] == (
        "materialized-agent",
        {
            "host": "0.0.0.0",
            "port": 9000,
            "mount_path": "/remote",
            "bearer_token": "secret",
        },
    )
    assert observed["serve_forever"] is True
    assert observed["closed"] is True
    assert observed["wait_closed"] is True


def test_target_resolution_reports_import_and_attribute_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_module(
        tmp_path,
        "sample_nested_attr",
        "\n".join(
            (
                "class Namespace:",
                "    def __init__(self) -> None:",
                "        self.child = object()",
                "",
                "root = Namespace()",
            )
        ),
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    original_import_module = importlib.import_module

    def fake_import_module(name: str, package: str | None = None) -> ModuleType:
        del package
        if name == "sample_import_broken":
            raise ImportError("broken import", name="json")
        return original_import_module(name)

    monkeypatch.setattr("acpkit.runtime.importlib.import_module", fake_import_module)

    with pytest.raises(AcpKitError, match="Could not import module `sample_import_broken`"):
        load_target("sample_import_broken:agent")

    with pytest.raises(AcpKitError, match="missing attribute `missing`"):
        load_target("sample_nested_attr:root.child.missing")


def test_target_resolution_reports_when_module_has_no_supported_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_module(
        tmp_path,
        "sample_no_agent_module",
        "\n".join(("value = 1", "other = 'demo'")),
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    _patch_adapter_modules(monkeypatch, available_modules={"pydantic_ai", "pydantic_acp"})

    with pytest.raises(UnsupportedAgentError, match="module defines no known agent instance"):
        load_target("sample_no_agent_module")


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


def test_cli_launch_command_reports_runtime_errors_for_target_and_raw_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_launch_target(
        target: str,
        *,
        import_roots: tuple[str, ...] | None = None,
    ) -> int:
        del target, import_roots
        raise MissingAdapterError.for_any_adapter()

    def raise_launch_command(command: str) -> int:
        del command
        raise MissingAdapterError.for_any_adapter()

    monkeypatch.setattr("acpkit.cli.launch_target", raise_launch_target)
    monkeypatch.setattr("acpkit.cli.launch_raw_command", raise_launch_command)

    target_result = CliRunner().invoke(cli, ["launch", "demo:agent"])
    command_result = CliRunner().invoke(cli, ["launch", "-c", "python3.11 finance_agent.py"])

    assert target_result.exit_code == 2
    assert "No ACP adapters are installed." in target_result.output
    assert command_result.exit_code == 2
    assert "No ACP adapters are installed." in command_result.output


def test_cli_launch_command_requires_exactly_one_mode() -> None:
    result = CliRunner().invoke(cli, ["launch"])

    assert result.exit_code == 2
    assert "Provide exactly one of `TARGET` or `--command`." in result.output


def test_cli_launch_command_rejects_path_with_raw_command() -> None:
    result = CliRunner().invoke(
        cli,
        ["launch", "-c", "python3.11 finance_agent.py", "-p", "/tmp/demo"],
    )

    assert result.exit_code == 2
    assert "`--path` can only be used when launching a target." in result.output


def test_cli_run_command_requires_exactly_one_mode() -> None:
    missing_result = CliRunner().invoke(cli, ["run"])
    duplicate_result = CliRunner().invoke(cli, ["run", "demo:agent", "--addr", "ws://example/ws"])

    assert missing_result.exit_code == 2
    assert "Provide exactly one of `TARGET` or `--addr`." in missing_result.output
    assert duplicate_result.exit_code == 2
    assert "Provide exactly one of `TARGET` or `--addr`." in duplicate_result.output


def test_cli_run_command_rejects_path_with_addr() -> None:
    result = CliRunner().invoke(
        cli,
        ["run", "--addr", "ws://agents.example.com/acp/ws", "-p", "/tmp/demo"],
    )

    assert result.exit_code == 2
    assert "`--path` can only be used with `TARGET`." in result.output


def test_root_remote_helpers_delegate_to_acpremote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}

    def fake_import_module(name: str, package: str | None = None) -> ModuleType:
        del package
        assert name == "acpremote"
        return cast(
            ModuleType,
            SimpleNamespace(
                connect_acp=lambda url, **kwargs: observed.setdefault("connect", (url, kwargs)),
                serve_acp=lambda agent, **kwargs: observed.setdefault("serve", (agent, kwargs)),
            ),
        )

    monkeypatch.setattr("acpkit.importlib.import_module", fake_import_module)

    connect_result = connect_acp("ws://agents.example.com/acp/ws", bearer_token="secret")
    serve_result = serve_acp("agent", mount_path="/remote")

    assert connect_result == (
        "ws://agents.example.com/acp/ws",
        {"bearer_token": "secret"},
    )
    assert serve_result == ("agent", {"mount_path": "/remote"})


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


def test_main_returns_exit_code_for_click_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_click_exit(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        import click

        raise click.exceptions.Exit(9)

    monkeypatch.setattr("acpkit.cli.cli.main", raise_click_exit)

    assert main(["launch", "demo:agent"]) == 9


def test_adapter_helpers_cover_none_and_negative_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert find_adapter_by_module_name(None) is None
    assert find_matching_adapter(object()) is None

    _patch_adapter_modules(monkeypatch, available_modules={"pydantic_ai", "pydantic_acp"})
    assert _missing_adapter_from_import_error(ImportError("boom", name="json")) is None
    assert _missing_adapter_from_import_error(ImportError("boom", name="pydantic_ai")) is None

    with pytest.raises(TypeError, match="Expected a `pydantic_ai.Agent` target."):
        _run_pydantic_target(object())
