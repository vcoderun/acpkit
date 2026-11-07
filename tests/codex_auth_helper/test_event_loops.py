from __future__ import annotations as _annotations

import asyncio
import gc
import json
from collections.abc import Awaitable, Iterator
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest
from codex_auth_helper import CodexAuthConfig, create_codex_async_openai

from .support import write_auth_file


class _KeepAliveHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    paths: list[str] = []

    def _reply(self, payload: dict[str, object]) -> None:
        self.paths.append(self.path)
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._reply({"object": "list", "data": []})

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self._reply({"access_token": "refreshed", "id_token": None, "refresh_token": "next"})

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@pytest.fixture
def server_url() -> Iterator[str]:
    _KeepAliveHandler.paths = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _KeepAliveHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _run(awaitable: Awaitable[object], errors: list[dict[str, Any]]) -> None:
    """Run on a new loop, as ``asyncio.run`` does, collecting errors the loop only reports."""

    async def main() -> None:
        asyncio.get_running_loop().set_exception_handler(lambda _, context: errors.append(context))
        await awaitable
        gc.collect()
        await asyncio.sleep(0)

    asyncio.run(main())


def _config(tmp_path: Path, url: str, *, refresh_every_call: bool = False) -> CodexAuthConfig:
    auth_path = tmp_path / "auth.json"
    write_auth_file(auth_path)
    return CodexAuthConfig(
        auth_path=auth_path,
        api_base_url=f"{url}/codex",
        issuer=url,
        # A margin longer than the token's lifetime makes every token request refresh.
        refresh_margin=timedelta(hours=2) if refresh_every_call else timedelta(seconds=30),
    )


def test_async_client_serves_runs_on_successive_event_loops(
    tmp_path: Path, server_url: str
) -> None:
    client = create_codex_async_openai(config=_config(tmp_path, server_url))

    errors: list[dict[str, Any]] = []

    _run(client.models.list(), errors)
    _run(client.models.list(), errors)
    _run(client.close(), errors)

    assert _KeepAliveHandler.paths == ["/codex/models", "/codex/models"]
    assert errors == []


def test_token_refresh_runs_on_successive_event_loops(tmp_path: Path, server_url: str) -> None:
    client = create_codex_async_openai(
        config=_config(tmp_path, server_url, refresh_every_call=True)
    )

    errors: list[dict[str, Any]] = []

    _run(client.token_manager.get_access_token(), errors)
    _run(client.token_manager.get_access_token(), errors)
    _run(client.close(), errors)

    assert _KeepAliveHandler.paths == ["/oauth/token", "/oauth/token"]
    assert errors == []
