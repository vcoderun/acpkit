from __future__ import annotations as _annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
from acpremote import TransportOptions
from acpremote import cli as cli_module
from acpremote.command import CommandOptions


@dataclass(slots=True)
class _FakeSocket:
    port: int = 4321

    def getsockname(self) -> tuple[str, int]:
        return ("127.0.0.1", self.port)


@dataclass(slots=True)
class _FakeNonTcpSocket:
    def getsockname(self) -> str:
        return "pipe"


@dataclass(slots=True)
class _FakeServer:
    sockets: list[_FakeSocket] = field(default_factory=lambda: [_FakeSocket()])
    serve_forever_calls: int = 0
    close_calls: int = 0
    wait_closed_calls: int = 0

    async def serve_forever(self) -> None:
        self.serve_forever_calls += 1

    def close(self) -> None:
        self.close_calls += 1

    async def wait_closed(self) -> None:
        self.wait_closed_calls += 1


def _write_native_agent_module(tmp_path: Path) -> None:
    (tmp_path / "native_cli_app.py").write_text(
        "\n".join(
            (
                "from __future__ import annotations as _annotations",
                "",
                "from typing import Any",
                "",
                "class NativeAgent:",
                "    async def initialize(self, *args: Any, **kwargs: Any) -> None:",
                "        return None",
                "    async def new_session(self, *args: Any, **kwargs: Any) -> None:",
                "        return None",
                "    async def prompt(self, *args: Any, **kwargs: Any) -> None:",
                "        return None",
                "    async def cancel(self, *args: Any, **kwargs: Any) -> None:",
                "        return None",
                "    def on_connect(self, conn: Any) -> None:",
                "        return None",
                "",
                "ignored = object()",
                "agent = NativeAgent()",
            ),
        )
        + "\n",
        encoding="utf-8",
    )


def test_acpremote_expose_command_builds_stdio_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    server = _FakeServer()
    calls: list[dict[str, Any]] = []
    monkeypatch.setenv("REMOTE_TOKEN", "secret")

    async def fake_serve_stdio_command(
        command_options: CommandOptions,
        **kwargs: Any,
    ) -> _FakeServer:
        calls.append({"command_options": command_options, "kwargs": kwargs})
        return server

    monkeypatch.setattr(cli_module, "serve_stdio_command", fake_serve_stdio_command)

    exit_code = cli_module.main(
        [
            "expose",
            "--host",
            "0.0.0.0",
            "--port",
            "9090",
            "--mount-path",
            "remote",
            "--token-env",
            "REMOTE_TOKEN",
            "--cwd",
            str(tmp_path),
            "--env",
            "A=1",
            "--env",
            "EMPTY=",
            "--stderr-mode",
            "discard",
            "--terminate-timeout",
            "1.5",
            "--",
            "python",
            "agent.py",
            "--flag",
        ],
    )

    assert exit_code == 0
    assert server.serve_forever_calls == 1
    assert server.close_calls == 1
    assert server.wait_closed_calls == 1
    command_options = cast("CommandOptions", calls[0]["command_options"])
    assert command_options.command == ("python", "agent.py", "--flag")
    assert command_options.cwd == str(tmp_path)
    assert command_options.env == {"A": "1", "EMPTY": ""}
    assert command_options.stderr_mode == "discard"
    assert command_options.terminate_timeout == 1.5
    assert calls[0]["kwargs"] == {
        "host": "0.0.0.0",
        "port": 9090,
        "mount_path": "remote",
        "bearer_token": "secret",
    }
    assert "ws://0.0.0.0:4321/remote/ws" in capsys.readouterr().err


def test_acpremote_serve_loads_native_agent_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_native_agent_module(tmp_path)
    server = _FakeServer()
    calls: list[dict[str, Any]] = []

    async def fake_serve_acp(agent: Any, **kwargs: Any) -> _FakeServer:
        calls.append({"agent": agent, "kwargs": kwargs})
        return server

    monkeypatch.setattr(cli_module, "serve_acp", fake_serve_acp)

    exit_code = cli_module.main(
        [
            "serve",
            "native_cli_app:agent",
            "--path",
            str(tmp_path),
            "--host",
            "127.0.0.1",
            "--port",
            "7070",
            "--mount-path",
            "/acp",
            "--bearer-token",
            "direct-token",
        ],
    )

    assert exit_code == 0
    assert calls[0]["agent"].__class__.__name__ == "NativeAgent"
    assert calls[0]["kwargs"] == {
        "host": "127.0.0.1",
        "port": 7070,
        "mount_path": "/acp",
        "bearer_token": "direct-token",
    }
    assert server.close_calls == 1


def test_acpremote_serve_uses_latest_native_agent_when_attribute_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_native_agent_module(tmp_path)
    calls: list[Any] = []

    async def fake_serve_acp(agent: Any, **kwargs: Any) -> _FakeServer:
        del kwargs
        calls.append(agent)
        return _FakeServer()

    monkeypatch.setattr(cli_module, "serve_acp", fake_serve_acp)

    exit_code = cli_module.main(["serve", "native_cli_app", "--path", str(tmp_path)])

    assert exit_code == 0
    assert calls[0].__class__.__name__ == "NativeAgent"


def test_acpremote_mirror_runs_remote_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy_agent = object()
    calls: list[Any] = []

    def fake_connect_acp(
        addr: str,
        *,
        bearer_token: str | None = None,
        options: TransportOptions | None = None,
    ) -> object:
        calls.append(("connect", addr, bearer_token, options))
        return proxy_agent

    async def fake_run_agent(agent: object) -> None:
        calls.append(("run", agent))

    monkeypatch.setattr(cli_module, "connect_acp", fake_connect_acp)
    monkeypatch.setattr(cli_module, "run_agent", fake_run_agent)

    exit_code = cli_module.main(["mirror", "ws://example.test/acp/ws", "--bearer-token", "token"])

    assert exit_code == 0
    assert calls == [
        ("connect", "ws://example.test/acp/ws", "token", None),
        ("run", proxy_agent),
    ]

    calls.clear()
    exit_code = cli_module.main(
        ["mirror", "ws://example.test/acp/ws", "--unstable-protocol"],
    )

    assert exit_code == 0
    assert calls == [
        (
            "connect",
            "ws://example.test/acp/ws",
            None,
            TransportOptions(use_unstable_protocol=True),
        ),
        ("run", proxy_agent),
    ]


def test_acpremote_cli_reports_user_errors(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_module.main(["expose"]) == 2
    assert "Command must not be empty." in capsys.readouterr().err

    assert cli_module.main(["expose", "--env", "INVALID", "python"]) == 2
    assert "Environment overrides must use `KEY=VALUE`." in capsys.readouterr().err

    assert cli_module.main(["mirror", "ws://example.test/acp/ws", "--token-env", "MISSING"]) == 2
    assert "`MISSING` is not set or is empty." in capsys.readouterr().err


def test_acpremote_serve_rejects_non_native_target(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "not_native.py").write_text("agent = object()\n", encoding="utf-8")

    exit_code = cli_module.main(["serve", "not_native:agent", "--path", str(tmp_path)])

    assert exit_code == 2
    assert "Target must resolve to a native `acp.interfaces.Agent`" in capsys.readouterr().err


def test_acpremote_cli_returns_130_on_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def interrupting_connect_acp(addr: str, *, bearer_token: str | None = None) -> object:
        del addr, bearer_token
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module, "connect_acp", interrupting_connect_acp)

    assert cli_module.main(["mirror", "ws://example.test/acp/ws"]) == 130


def test_acpremote_cli_banner_handles_servers_without_tcp_port(
    capsys: pytest.CaptureFixture[str],
) -> None:
    server_without_sockets = _FakeServer(sockets=[])
    server_with_non_tcp_socket = _FakeServer(sockets=[cast("Any", _FakeNonTcpSocket())])

    cli_module._print_server_banner(server_without_sockets, host="127.0.0.1", mount_path="/acp")

    assert "ws://127.0.0.1/acp/ws" in capsys.readouterr().err
    assert cli_module._server_port(server_with_non_tcp_socket) is None


def test_acpremote_cli_private_validation_paths(tmp_path: Path) -> None:
    assert cli_module._parse_env_overrides(()) is None

    with pytest.raises(cli_module.AcpRemoteCliError, match="module name"):
        cli_module._parse_target_ref("")
    with pytest.raises(cli_module.AcpRemoteCliError, match="attribute cannot be empty"):
        cli_module._parse_target_ref("demo:")
    with pytest.raises(cli_module.AcpRemoteCliError, match="Could not import module"):
        cli_module._load_target("missing_cli_module:agent", import_roots=())

    (tmp_path / "empty_cli_app.py").write_text("value = object()\n", encoding="utf-8")

    with pytest.raises(cli_module.AcpRemoteCliError, match="defines no native ACP agent"):
        cli_module._load_target("empty_cli_app", import_roots=(str(tmp_path),))
    with pytest.raises(cli_module.AcpRemoteCliError, match="missing attribute"):
        cli_module._load_target("empty_cli_app:agent", import_roots=(str(tmp_path),))
