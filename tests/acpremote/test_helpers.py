from __future__ import annotations as _annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast

import pytest
from acp.schema import (
    AuthenticateResponse,
    ClientCapabilities,
    CloseSessionResponse,
    ForkSessionResponse,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    NewSessionResponse,
    PromptResponse,
    ResumeSessionResponse,
    SetSessionConfigOptionResponse,
    SetSessionModelResponse,
    SetSessionModeResponse,
)
from acpremote import client as client_module
from acpremote import command as command_module
from acpremote import proxy_agent as proxy_agent_module
from acpremote import stream as stream_module
from acpremote.client import RemoteClientConnection
from acpremote.config import TransportOptions
from acpremote.metadata import ServerMetadata
from websockets.exceptions import ConnectionClosedOK


@dataclass(slots=True)
class _FakeHttpResponse:
    status: int = 200
    body: bytes = b"{}"

    def read(self) -> bytes:
        return self.body


@dataclass(slots=True)
class _FakeHttpConnection:
    response: _FakeHttpResponse
    request_error: Exception | None = None
    request_calls: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)
    closed: bool = False

    def request(self, method: str, path: str, headers: dict[str, str]) -> None:
        if self.request_error is not None:
            raise self.request_error
        self.request_calls.append((method, path, headers))

    def getresponse(self) -> _FakeHttpResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


@dataclass(slots=True)
class _FakeClient:
    permission_calls: list[dict[str, Any]] = field(default_factory=list)
    updates: list[dict[str, Any]] = field(default_factory=list)
    notifications: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def request_permission(
        self,
        options: list[Any],
        session_id: str,
        tool_call: Any,
        **kwargs: Any,
    ) -> str:
        self.permission_calls.append(
            {
                "options": options,
                "session_id": session_id,
                "tool_call": tool_call,
                "kwargs": kwargs,
            }
        )
        return "ok"

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.updates.append({"session_id": session_id, "update": update, "kwargs": kwargs})

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"method": method, "params": params}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        self.notifications.append((method, params))


@dataclass(slots=True)
class _FakeRemoteMethods:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def initialize(self, protocol_version: int, **kwargs: Any) -> InitializeResponse:
        self.calls.append(
            ("initialize", {"protocol_version": protocol_version, **kwargs})
        )  # pragma: no cover
        return InitializeResponse(protocol_version=protocol_version)  # pragma: no cover

    async def new_session(self, **kwargs: Any) -> NewSessionResponse:
        self.calls.append(("new_session", kwargs))  # pragma: no cover
        return NewSessionResponse(session_id="remote-session")  # pragma: no cover

    async def load_session(self, **kwargs: Any) -> LoadSessionResponse | None:
        self.calls.append(("load_session", kwargs))
        return cast(LoadSessionResponse, {"ok": True})

    async def list_sessions(self, **kwargs: Any) -> ListSessionsResponse:
        self.calls.append(("list_sessions", kwargs))
        return cast(ListSessionsResponse, {"sessions": []})

    async def set_session_mode(self, **kwargs: Any) -> SetSessionModeResponse | None:
        self.calls.append(("set_session_mode", kwargs))
        return cast(SetSessionModeResponse, {"mode": kwargs["mode_id"]})

    async def set_session_model(self, **kwargs: Any) -> SetSessionModelResponse | None:
        self.calls.append(("set_session_model", kwargs))
        return cast(SetSessionModelResponse, {"model": kwargs["model_id"]})

    async def set_config_option(self, **kwargs: Any) -> SetSessionConfigOptionResponse | None:
        self.calls.append(("set_config_option", kwargs))
        return cast(SetSessionConfigOptionResponse, {"config": kwargs["config_id"]})

    async def authenticate(self, **kwargs: Any) -> AuthenticateResponse | None:
        self.calls.append(("authenticate", kwargs))
        return AuthenticateResponse()

    async def prompt(self, **kwargs: Any) -> PromptResponse:
        self.calls.append(("prompt", kwargs))
        return PromptResponse(stop_reason="end_turn")

    async def fork_session(self, **kwargs: Any) -> ForkSessionResponse:
        self.calls.append(("fork_session", kwargs))
        return cast(ForkSessionResponse, {"session_id": kwargs["session_id"]})

    async def resume_session(self, **kwargs: Any) -> ResumeSessionResponse:
        self.calls.append(("resume_session", kwargs))
        return cast(ResumeSessionResponse, {"session_id": kwargs["session_id"]})

    async def close_session(self, **kwargs: Any) -> CloseSessionResponse | None:
        self.calls.append(("close_session", kwargs))
        return CloseSessionResponse()

    async def cancel(self, **kwargs: Any) -> None:
        self.calls.append(("cancel", kwargs))

    async def ext_method(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("ext_method", kwargs))
        return kwargs

    async def ext_notification(self, **kwargs: Any) -> None:
        self.calls.append(("ext_notification", kwargs))


@dataclass(slots=True)
class _FakeStdin:
    writes: list[bytes] = field(default_factory=list)
    closed: bool = False
    wait_closed_error: Exception | None = None

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def is_closing(self) -> bool:
        return self.closed

    async def wait_closed(self) -> None:
        if self.wait_closed_error is not None:
            raise self.wait_closed_error


@dataclass(slots=True)
class _FakeStdout:
    lines: list[bytes]

    async def readline(self) -> bytes:
        if not self.lines:
            return b""
        return self.lines.pop(0)


@dataclass(slots=True)
class _FakeCommandWebSocket:
    messages: list[str | bytes]
    sent: list[str] = field(default_factory=list)
    close_calls: int = 0

    async def recv(self) -> str | bytes:
        if not self.messages:
            raise ConnectionClosedOK(None, None)
        return self.messages.pop(0)

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        self.close_calls += 1


@dataclass(slots=True)
class _FakeStreamWebSocket:
    incoming: list[str | bytes] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)
    send_error: BaseException | None = None
    close_calls: int = 0
    wait_closed_calls: int = 0

    async def recv(self, decode: bool | None = None) -> str | bytes:
        del decode
        if not self.incoming:
            raise ConnectionClosedOK(None, None)
        return self.incoming.pop(0)  # pragma: no cover

    async def send(self, message: str) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(message)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        del code, reason
        self.close_calls += 1

    async def wait_closed(self) -> None:
        self.wait_closed_calls += 1


@pytest.mark.asyncio
async def test_helper_fakes_cover_unreached_stub_paths() -> None:
    methods = _FakeRemoteMethods()
    assert isinstance(await methods.initialize(protocol_version=1), InitializeResponse)
    assert (await methods.new_session(cwd="/tmp")).session_id == "remote-session"
    assert await methods.load_session(session_id="s-1") == {"ok": True}
    assert await methods.list_sessions(cursor="c-1") == {"sessions": []}
    assert await methods.set_session_mode(session_id="s-1", mode_id="ask") == {"mode": "ask"}
    assert await methods.set_session_model(session_id="s-1", model_id="model-a") == {
        "model": "model-a"
    }
    assert await methods.set_config_option(session_id="s-1", config_id="flag") == {"config": "flag"}
    assert isinstance(await methods.authenticate(method_id="demo"), AuthenticateResponse)
    assert await methods.fork_session(session_id="s-1") == {"session_id": "s-1"}
    assert await methods.resume_session(session_id="s-1") == {"session_id": "s-1"}
    assert isinstance(await methods.close_session(session_id="s-1"), CloseSessionResponse)
    await methods.cancel(session_id="s-1")
    assert await methods.ext_method(method="demo.echo", params={"value": 1}) == {
        "method": "demo.echo",
        "params": {"value": 1},
    }
    await methods.ext_notification(method="demo.note", params={"value": 2})

    stdin = _FakeStdin()
    stdin.write(b"hello")
    await stdin.drain()
    stdin.close()
    assert stdin.is_closing() is True
    await stdin.wait_closed()
    stdin.wait_closed_error = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        await stdin.wait_closed()

    stdout = _FakeStdout(lines=[])
    assert await stdout.readline() == b""

    command_socket = _FakeCommandWebSocket(messages=[])
    with pytest.raises(ConnectionClosedOK):
        await command_socket.recv()
    await command_socket.close()
    assert command_socket.close_calls == 1

    stream_socket = _FakeStreamWebSocket(incoming=["message"])
    assert await stream_socket.recv() == "message"
    with pytest.raises(ConnectionClosedOK):
        await stream_socket.recv()
    await stream_socket.close()
    await stream_socket.wait_closed()
    assert stream_socket.close_calls == 1
    assert stream_socket.wait_closed_calls == 1


@pytest.mark.asyncio
async def test_client_helper_paths_cover_metadata_edge_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert await client_module.fetch_server_metadata("http://example.com/acp/ws") is None
    assert client_module._merge_headers({"X-Test": "1"}, {"Authorization": "Bearer token"}) == [
        ("X-Test", "1"),
        ("Authorization", "Bearer token"),
    ]
    assert client_module._metadata_url("ftp://example.com/acp/ws") is None
    assert client_module._metadata_url("ws://example.com/acp") is None
    assert client_module._metadata_url("wss://example.com/ws") == "https://example.com/"
    assert client_module._fetch_server_metadata_sync("http:///metadata", None) is None

    bad_status = _FakeHttpConnection(response=_FakeHttpResponse(status=503))
    invalid_json = _FakeHttpConnection(response=_FakeHttpResponse(body=b"{not-json"))
    non_dict = _FakeHttpConnection(response=_FakeHttpResponse(body=b"[]"))
    missing_keys = _FakeHttpConnection(response=_FakeHttpResponse(body=b'{"transport_kind":"ws"}'))
    good_payload = _FakeHttpConnection(
        response=_FakeHttpResponse(
            body=(
                b'{"transport_kind":"websocket","transport_version":"1","package_version":"0.8.4",'
                b'"auth_required":1,"supported_auth_modes":"bearer","max_size":"12","max_queue":"4",'
                b'"compression":5,"health_path":"/healthz","metadata_path":"/acp",'
                b'"websocket_path":"/acp/ws","supported_agent_families":"none","remote_cwd":123}'
            )
        )
    )
    broken_request = _FakeHttpConnection(
        response=_FakeHttpResponse(),
        request_error=OSError("boom"),
    )
    queued_connections = iter(
        [
            bad_status,
            invalid_json,
            non_dict,
            missing_keys,
            good_payload,
            broken_request,
        ]
    )

    monkeypatch.setattr(
        client_module, "HTTPConnection", lambda host, port: next(queued_connections)
    )
    monkeypatch.setattr(
        client_module, "HTTPSConnection", lambda host, port: next(queued_connections)
    )

    assert (
        client_module._fetch_server_metadata_sync(
            "http://example.com/acp",
            [("Authorization", "Bearer token")],
        )
        is None
    )
    assert client_module._fetch_server_metadata_sync("http://example.com/acp", None) is None
    assert client_module._fetch_server_metadata_sync("http://example.com/acp", None) is None
    assert client_module._fetch_server_metadata_sync("https://example.com/acp", None) is None
    metadata = client_module._fetch_server_metadata_sync("http://example.com/acp", None)
    assert metadata == ServerMetadata(
        transport_kind="websocket",
        transport_version=1,
        package_version="0.8.4",
        auth_required=True,
        supported_auth_modes=(),
        max_size=12,
        max_queue=4,
        compression="5",
        health_path="/healthz",
        metadata_path="/acp",
        websocket_path="/acp/ws",
        supported_agent_families=(),
        remote_cwd="123",
    )
    assert client_module._fetch_server_metadata_sync("http://example.com/acp", None) is None
    assert client_module._headers_dict([("X-Test", "1")]) == {"X-Test": "1"}
    assert client_module._string_list("not-a-list") == []
    assert client_module._optional_str(123) == "123"
    assert good_payload.request_calls[0] == ("GET", "/acp", {})
    assert good_payload.closed is True


@pytest.mark.asyncio
async def test_client_remote_connection_close_and_metadata_fetch_thread_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []

    @dataclass(slots=True)
    class _FakeConnection:
        async def close(self) -> None:
            closed.append("connection")

    @dataclass(slots=True)
    class _FakeStreams:
        async def close(self) -> None:
            closed.append("streams")

    remote = RemoteClientConnection(
        connection=cast(Any, _FakeConnection()),
        websocket=cast(Any, object()),
        streams=cast(Any, _FakeStreams()),
    )
    await remote.close()
    assert closed == ["connection", "streams"]

    async def fake_to_thread(func: Any, metadata_url: str, headers: Any) -> str:
        assert metadata_url == "http://example.com/acp"
        assert headers == {"X-Test": "1"}
        return "ok"

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    result = await client_module.fetch_server_metadata(
        "ws://example.com/acp/ws",
        headers={"X-Test": "1"},
    )
    assert result == "ok"


@pytest.mark.asyncio
async def test_command_helper_paths_cover_process_relay_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = command_module._build_process_env(None)
    assert env["PATH"]

    fake_stdin = _FakeStdin()
    with pytest.raises(TypeError, match="binary WebSocket frames"):
        await command_module._relay_websocket_to_stdin(
            cast(Any, _FakeCommandWebSocket(messages=[b"binary"])),
            cast(Any, fake_stdin),
        )
    assert fake_stdin.closed is True

    stdout_websocket = _FakeCommandWebSocket(messages=[])
    await command_module._relay_stdout_to_websocket(
        cast(Any, _FakeStdout(lines=[b"first\n", b"second", b""])),
        cast(Any, stdout_websocket),
    )
    assert stdout_websocket.sent == ["first", "second"]

    already_closed = _FakeStdin()
    already_closed.close()
    await command_module._close_stdin(cast(Any, already_closed))
    assert already_closed.closed is True

    broken_pipe = _FakeStdin(wait_closed_error=BrokenPipeError())
    await command_module._close_stdin(cast(Any, broken_pipe))
    assert broken_pipe.closed is True

    connection_reset = _FakeStdin(wait_closed_error=ConnectionResetError())
    await command_module._close_stdin(cast(Any, connection_reset))
    assert connection_reset.closed is True


@pytest.mark.asyncio
async def test_command_process_creation_and_raise_if_needed_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_create_subprocess_exec(*args: Any, **kwargs: Any) -> object:
        calls.append({"args": args, "kwargs": kwargs})
        return object()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await command_module._create_command_process(
        command_options=command_module.CommandOptions(command=("echo", "hi")),
        reader_limit=128,
    )
    await command_module._create_command_process(
        command_options=command_module.CommandOptions(
            command=("echo", "hi"),
            stderr_mode="discard",
        ),
        reader_limit=256,
    )

    assert calls[0]["kwargs"]["stderr"] is None
    assert calls[0]["kwargs"]["limit"] == 128
    assert calls[1]["kwargs"]["stderr"] == asyncio.subprocess.DEVNULL
    assert calls[1]["kwargs"]["limit"] == 256

    async def _raises(exc: BaseException) -> None:
        raise exc

    for exc in (
        cast(BaseException, ConnectionClosedOK(None, None)),
        BrokenPipeError(),
        ConnectionResetError(),
        ProcessLookupError(),
    ):
        task = asyncio.create_task(_raises(exc))
        with contextlib.suppress(type(exc)):
            await task
        command_module._raise_if_needed(cast(asyncio.Task[object], task))

    cancelled = asyncio.create_task(asyncio.sleep(1))
    await asyncio.sleep(0)
    cancelled.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await cancelled
    with pytest.raises(asyncio.CancelledError):
        command_module._raise_if_needed(cast(asyncio.Task[object], cancelled))


@pytest.mark.asyncio
async def test_command_connection_covers_first_completed_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    close_calls: list[str] = []
    stdin_close_calls: list[str] = []

    async def fake_close_websocket(websocket: Any) -> None:
        del websocket
        close_calls.append("websocket")

    async def fake_close_stdin(stdin: Any) -> None:
        del stdin
        stdin_close_calls.append("stdin")

    monkeypatch.setattr(command_module, "_close_websocket", fake_close_websocket)
    monkeypatch.setattr(command_module, "_close_stdin", fake_close_stdin)

    @dataclass(slots=True)
    class _FakeProcess:
        stdin: Any = field(default_factory=object)
        stdout: Any = field(default_factory=object)
        returncode: int | None = None
        terminate_calls: int = 0
        wait_delay: float = 0.0
        wait_error: Exception | None = None

        def terminate(self) -> None:
            self.terminate_calls += 1

        async def wait(self) -> int:
            await asyncio.sleep(self.wait_delay)
            if self.wait_error is not None:
                raise self.wait_error
            if self.returncode is None:  # pragma: no branch
                self.returncode = 0
            return self.returncode

    async def done_immediately(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        return None

    async def done_later(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        await asyncio.sleep(0.01)

    fast_exit = _FakeProcess(wait_delay=0.0)

    async def create_fast_process(**kwargs: Any) -> _FakeProcess:
        del kwargs
        return fast_exit

    monkeypatch.setattr(command_module, "_create_command_process", create_fast_process)
    monkeypatch.setattr(command_module, "_relay_websocket_to_stdin", done_later)
    monkeypatch.setattr(command_module, "_relay_stdout_to_websocket", done_later)
    await command_module.run_remote_command_connection(
        cast(Any, object()),
        command_options=command_module.CommandOptions(command=("echo", "hi")),
    )
    assert close_calls

    close_calls.clear()
    stdin_close_calls.clear()
    delayed_exit = _FakeProcess(wait_delay=0.01, wait_error=ProcessLookupError())

    async def create_delayed_process(**kwargs: Any) -> _FakeProcess:
        del kwargs
        return delayed_exit

    monkeypatch.setattr(command_module, "_create_command_process", create_delayed_process)
    monkeypatch.setattr(command_module, "_relay_websocket_to_stdin", done_immediately)
    monkeypatch.setattr(command_module, "_relay_stdout_to_websocket", done_later)
    await command_module.run_remote_command_connection(
        cast(Any, object()),
        command_options=command_module.CommandOptions(command=("echo", "hi")),
    )
    assert delayed_exit.terminate_calls >= 1
    assert close_calls
    assert stdin_close_calls

    close_calls.clear()
    stdin_close_calls.clear()

    async def all_done(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        return None

    instant_exit = _FakeProcess(wait_delay=0.0)

    async def create_instant_process(**kwargs: Any) -> _FakeProcess:
        del kwargs
        return instant_exit

    monkeypatch.setattr(command_module, "_create_command_process", create_instant_process)
    monkeypatch.setattr(command_module, "_relay_websocket_to_stdin", all_done)
    monkeypatch.setattr(command_module, "_relay_stdout_to_websocket", all_done)
    await command_module.run_remote_command_connection(
        cast(Any, object()),
        command_options=command_module.CommandOptions(command=("echo", "hi")),
    )

    async def failing_relay(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise ValueError("boom")

    async def slow_relay(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        await asyncio.sleep(0.05)

    hanging_exit = _FakeProcess(wait_delay=0.05)

    async def create_hanging_process(**kwargs: Any) -> _FakeProcess:
        del kwargs
        return hanging_exit

    monkeypatch.setattr(command_module, "_create_command_process", create_hanging_process)
    monkeypatch.setattr(command_module, "_relay_websocket_to_stdin", failing_relay)
    monkeypatch.setattr(command_module, "_relay_stdout_to_websocket", slow_relay)
    with pytest.raises(ValueError, match="boom"):
        await command_module.run_remote_command_connection(
            cast(Any, object()),
            command_options=command_module.CommandOptions(command=("echo", "hi")),
        )


@pytest.mark.asyncio
async def test_proxy_agent_helper_paths_cover_delegate_and_metadata_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = proxy_agent_module._TransportLatencyTracker()
    assert tracker.update_meta("missing") is None
    assert tracker.finish_prompt("missing") is None
    tracker.start_prompt("session-1")
    assert tracker.update_meta("session-1") is not None
    second_meta = tracker.update_meta("session-1")
    assert second_meta is not None
    assert second_meta["acpremote"]["transport_latency"]["update_count"] == 2
    snapshot = tracker.finish_prompt("session-1")
    assert snapshot is not None
    assert "First remote update:" in proxy_agent_module._format_latency_summary(snapshot)

    client = _FakeClient()
    latency_client = proxy_agent_module._LatencyClient(
        delegate=cast(Any, client),
        tracker=tracker,
        emit_latency_meta=False,
    )
    assert latency_client._meta("missing") is None
    assert await latency_client.request_permission([], "session-1", object()) == "ok"
    await latency_client.session_update("session-1", {"ok": True})
    assert await latency_client.ext_method("demo.echo", {"value": 1}) == {
        "method": "demo.echo",
        "params": {"value": 1},
    }
    await latency_client.ext_notification("demo.note", {"value": 2})

    assert proxy_agent_module._merge_field_meta({"source": "x"}, None) == {"source": "x"}
    assert proxy_agent_module._merge_field_meta({}, {"acpremote": {"value": 1}}) == {
        "field_meta": {"acpremote": {"value": 1}}
    }
    assert proxy_agent_module._merge_field_meta(
        {"field_meta": "not-a-dict"},
        {"acpremote": {"value": 1}},
    ) == {"field_meta": "not-a-dict"}
    assert proxy_agent_module._merge_field_meta(
        {"field_meta": {"acpremote": {"before": 1}}},
        {"acpremote": {"after": 2}},
    ) == {"field_meta": {"acpremote": {"before": 1, "after": 2}}}
    assert proxy_agent_module._merge_field_meta(
        {"field_meta": {"acpremote": "scalar"}},
        {"acpremote": {"after": 2}},
    ) == {"field_meta": {"acpremote": {"after": 2}}}

    caps = ClientCapabilities(fs=None, terminal=True)
    resolved_caps = proxy_agent_module._resolved_client_capabilities(
        caps,
        host_ownership="remote",
    )
    assert isinstance(resolved_caps, ClientCapabilities)
    assert resolved_caps.fs is None
    assert resolved_caps.terminal is False
    assert (
        proxy_agent_module._resolved_client_capabilities(
            caps,
            host_ownership="client_passthrough",
        )
        is caps
    )
    assert proxy_agent_module._resolved_client_capabilities(
        {"value": 1},
        host_ownership="remote",
    ) == {"value": 1}

    proxy = proxy_agent_module.RemoteProxyAgent(url="ws://example.invalid/acp/ws")
    await proxy.close()
    assert proxy._resolve_optional_cwd("/tmp") == "/tmp"
    await proxy._emit_latency_projection(
        "session-1",
        proxy_agent_module._PromptLatencySnapshot(
            tool_call_id="tool-1",
            total_ms=1,
            first_update_ms=None,
            update_count=0,
        ),
    )
    projection_proxy = proxy_agent_module.RemoteProxyAgent(
        url="ws://example.invalid/acp/ws",
        options=TransportOptions(emit_latency_projection=True),
    )
    await projection_proxy._emit_latency_projection(
        "session-1",
        proxy_agent_module._PromptLatencySnapshot(
            tool_call_id="tool-2",
            total_ms=1,
            first_update_ms=1,
            update_count=1,
        ),
    )

    closers: list[str] = []

    @dataclass(slots=True)
    class _ClosableRemote:
        async def close(self) -> None:
            closers.append("closed")  # pragma: no cover

    proxy._client = cast(Any, object())
    proxy._remote = cast(RemoteClientConnection, _ClosableRemote())
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: (_ for _ in ()).throw(RuntimeError()))
    proxy.on_connect(cast(Any, object()))
    assert proxy._remote is None

    class _FakeLoop:
        def __init__(self) -> None:
            self.created = 0

        def create_task(self, coro: Any) -> None:
            self.created += 1
            coro.close()

    fake_loop = _FakeLoop()
    proxy._client = cast(Any, object())
    proxy._remote = cast(RemoteClientConnection, _ClosableRemote())
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)
    proxy.on_connect(cast(Any, object()))
    assert fake_loop.created == 1
    assert proxy._remote is None


@pytest.mark.asyncio
async def test_proxy_agent_methods_cover_forwarding_and_connection_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    methods = _FakeRemoteMethods()

    async def _close_remote() -> None:
        return None  # pragma: no cover

    remote = cast(
        RemoteClientConnection,
        SimpleNamespace(
            connection=methods,
            metadata=ServerMetadata(
                transport_kind="websocket",
                transport_version=1,
                package_version="0.8.4",
                auth_required=False,
                supported_auth_modes=(),
                max_size=1,
                max_queue=1,
                compression=None,
                health_path="/healthz",
                metadata_path="/acp",
                websocket_path="/acp/ws",
                supported_agent_families=(),
                remote_cwd="/srv/remote",
            ),
            close=_close_remote,
        ),
    )
    proxy = proxy_agent_module.RemoteProxyAgent(
        url="ws://example.invalid/acp/ws",
        options=TransportOptions(),
    )
    proxy._client = cast(Any, object())
    proxy._remote = remote
    proxy._remote_cwd = "/srv/remote"

    await proxy.load_session(cwd="/tmp", session_id="sess")
    await proxy.set_session_mode(mode_id="fast", session_id="sess")
    await proxy.set_session_model(model_id="gpt-5.4", session_id="sess")
    await proxy.set_config_option(config_id="thinking", session_id="sess", value=True)
    await proxy.authenticate(method_id="bearer")
    response = await proxy.prompt(prompt=[], session_id="sess")
    assert response.stop_reason == "end_turn"
    await proxy.fork_session(cwd="/tmp", session_id="sess")
    await proxy.resume_session(cwd="/tmp", session_id="sess")
    await proxy.close_session(session_id="sess")
    await proxy.cancel(session_id="sess")
    assert await proxy.ext_method("demo.echo", {"value": 1}) == {
        "method": "demo.echo",
        "params": {"value": 1},
    }
    await proxy.ext_notification("demo.note", {"value": 2})

    forwarded = dict(methods.calls)
    assert forwarded["load_session"]["cwd"] == "/srv/remote"
    assert forwarded["fork_session"]["cwd"] == "/srv/remote"
    assert forwarded["resume_session"]["cwd"] == "/srv/remote"
    assert forwarded["set_session_mode"]["mode_id"] == "fast"
    assert forwarded["set_session_model"]["model_id"] == "gpt-5.4"
    assert forwarded["set_config_option"]["value"] is True
    assert forwarded["authenticate"]["method_id"] == "bearer"
    assert forwarded["close_session"]["session_id"] == "sess"
    assert forwarded["cancel"]["session_id"] == "sess"

    proxy._remote = None
    proxy._remote_cwd = None

    class _PreloadedLock:
        def __init__(self, target: proxy_agent_module.RemoteProxyAgent) -> None:
            self._target = target

        async def __aenter__(self) -> None:
            self._target._remote = remote

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            del exc_type, exc, tb
            return False

    proxy._connect_lock = cast(Any, _PreloadedLock(proxy))
    assert await proxy._remote_connection() is remote

    prompt_proxy = proxy_agent_module.RemoteProxyAgent(url="ws://example.invalid/acp/ws")
    prompt_proxy._client = cast(Any, object())
    prompt_proxy._remote = remote
    prompt_proxy._latency_tracker = cast(
        Any,
        SimpleNamespace(
            start_prompt=lambda session_id: None,
            finish_prompt=lambda session_id: None,
        ),
    )
    response = await prompt_proxy.prompt(prompt=[], session_id="sess")
    assert response.stop_reason == "end_turn"

    prompt_proxy._client = None
    await prompt_proxy._emit_latency_projection(
        "sess",
        proxy_agent_module._PromptLatencySnapshot(
            tool_call_id="tool-1",
            total_ms=1,
            first_update_ms=1,
            update_count=1,
        ),
    )


@pytest.mark.asyncio
async def test_stream_helper_paths_cover_protocol_and_sender_edge_cases() -> None:
    loop = asyncio.get_running_loop()
    protocol = stream_module._WriterProtocol(loop=loop)
    await protocol._drain_helper()
    waiter = protocol._get_close_waiter(cast(asyncio.StreamWriter, object()))
    protocol.connection_lost(None)
    await waiter

    completed = stream_module._WriterProtocol(loop=loop)
    completed.connection_lost(None)
    completed.connection_lost(RuntimeError("ignored"))

    websocket = _FakeStreamWebSocket()
    transport = stream_module._WebSocketTransport(
        websocket=cast(Any, websocket),
        loop=loop,
        protocol=protocol,
    )
    transport._sender_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await transport._sender_task

    first_future: asyncio.Future[None] = loop.create_future()
    first_future.set_result(None)
    second_future: asyncio.Future[None] = loop.create_future()
    transport._pending.put_nowait(stream_module._PendingWrite(payload="first", future=first_future))
    transport._pending.put_nowait(
        stream_module._PendingWrite(payload="second", future=second_future)
    )
    transport._pending.put_nowait(None)
    await transport._sender_loop()
    assert websocket.sent == ["first", "second"]
    assert second_future.done() is True

    error_websocket = _FakeStreamWebSocket(send_error=ConnectionResetError("boom"))
    error_protocol = stream_module._WriterProtocol(loop=loop)
    error_transport = stream_module._WebSocketTransport(
        websocket=cast(Any, error_websocket),
        loop=loop,
        protocol=error_protocol,
    )
    error_transport._sender_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await error_transport._sender_task

    failed_future: asyncio.Future[None] = loop.create_future()
    pending_future: asyncio.Future[None] = loop.create_future()
    error_transport._pending.put_nowait(
        stream_module._PendingWrite(payload="fail", future=failed_future)
    )
    error_transport._pending.put_nowait(
        stream_module._PendingWrite(payload="pending", future=pending_future)
    )
    error_transport._pending.put_nowait(None)
    await error_transport._sender_loop()
    assert failed_future.done() is True
    assert pending_future.done() is True
    assert isinstance(pending_future.exception(), ConnectionResetError)

    cancelled_websocket = _FakeStreamWebSocket(send_error=asyncio.CancelledError())
    cancelled_protocol = stream_module._WriterProtocol(loop=loop)
    cancelled_transport = stream_module._WebSocketTransport(
        websocket=cast(Any, cancelled_websocket),
        loop=loop,
        protocol=cancelled_protocol,
    )
    cancelled_transport._sender_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await cancelled_transport._sender_task
    cancelled_future: asyncio.Future[None] = loop.create_future()
    cancelled_transport._pending.put_nowait(
        stream_module._PendingWrite(payload="cancel", future=cancelled_future)
    )
    with pytest.raises(asyncio.CancelledError):
        await cancelled_transport._sender_loop()
