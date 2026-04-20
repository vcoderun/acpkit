from __future__ import annotations as _annotations

import json
import os
from http import HTTPStatus
from typing import Literal
from urllib.parse import urlsplit

from acp import run_agent
from acp.interfaces import Agent
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from .auth import is_bearer_authorized
from .command import CommandOptions, run_remote_command_connection
from .config import ServerOptions, TransportOptions
from .metadata import ServerMetadata, build_server_metadata
from .stream import open_websocket_stream_bridge

__all__ = (
    "run_remote_agent_connection",
    "serve_acp",
    "serve_command",
    "serve_remote_agent",
    "serve_stdio_command",
)


async def run_remote_agent_connection(
    agent: Agent,
    websocket: ServerConnection,
    *,
    options: TransportOptions | None = None,
) -> None:
    resolved_options = options or TransportOptions()
    bridge = await open_websocket_stream_bridge(
        websocket,
        reader_limit=resolved_options.reader_limit,
    )
    try:
        await run_agent(agent, input_stream=bridge.writer, output_stream=bridge.reader)
    finally:
        await bridge.close()


async def serve_remote_agent(
    agent: Agent,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    options: TransportOptions | None = None,
    server_options: ServerOptions | None = None,
) -> Server:
    if options is not None and server_options is not None:
        raise ValueError("pass either options or server_options, not both")
    resolved_server_options = server_options or ServerOptions(
        transport=options or TransportOptions()
    )
    resolved_transport = resolved_server_options.transport
    metadata = build_server_metadata(resolved_server_options)

    return await serve(
        lambda websocket: run_remote_agent_connection(
            agent,
            websocket,
            options=resolved_transport,
        ),
        host,
        port,
        process_request=lambda connection, request: _process_request(
            connection,
            request,
            server_options=resolved_server_options,
            metadata=metadata,
        ),
        compression=resolved_transport.compression,
        open_timeout=resolved_transport.open_timeout,
        ping_interval=resolved_transport.ping_interval,
        ping_timeout=resolved_transport.ping_timeout,
        close_timeout=resolved_transport.close_timeout,
        max_size=resolved_transport.max_size,
        max_queue=resolved_transport.max_queue,
    )


async def serve_acp(
    agent: Agent,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    mount_path: str = "/acp",
    bearer_token: str | None = None,
    options: TransportOptions | None = None,
    supported_agent_families: tuple[str, ...] = (),
    remote_cwd: str | None = None,
) -> Server:
    return await serve_remote_agent(
        agent,
        host=host,
        port=port,
        server_options=ServerOptions(
            mount_path=mount_path,
            bearer_token=bearer_token,
            supported_agent_families=supported_agent_families,
            remote_cwd=remote_cwd,
            transport=options or TransportOptions(),
        ),
    )


async def serve_command(
    command: tuple[str, ...] | list[str],
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    mount_path: str = "/acp",
    bearer_token: str | None = None,
    options: TransportOptions | None = None,
    supported_agent_families: tuple[str, ...] = (),
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    stderr_mode: Literal["inherit", "discard"] = "inherit",
) -> Server:
    command_options = CommandOptions(
        command=tuple(command),
        cwd=cwd,
        env=env,
        stderr_mode=stderr_mode,
    )
    return await serve_stdio_command(
        command_options,
        host=host,
        port=port,
        mount_path=mount_path,
        bearer_token=bearer_token,
        options=options,
        supported_agent_families=supported_agent_families,
    )


async def serve_stdio_command(
    command_options: CommandOptions,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    mount_path: str = "/acp",
    bearer_token: str | None = None,
    options: TransportOptions | None = None,
    supported_agent_families: tuple[str, ...] = (),
) -> Server:
    server_options = ServerOptions(
        mount_path=mount_path,
        bearer_token=bearer_token,
        supported_agent_families=supported_agent_families,
        remote_cwd=command_options.cwd or os.getcwd(),
        transport=options or TransportOptions(),
    )
    metadata = build_server_metadata(server_options)
    resolved_transport = server_options.transport

    return await serve(
        lambda websocket: run_remote_command_connection(
            websocket,
            command_options=command_options,
            transport_options=resolved_transport,
        ),
        host,
        port,
        process_request=lambda connection, request: _process_request(
            connection,
            request,
            server_options=server_options,
            metadata=metadata,
        ),
        compression=resolved_transport.compression,
        open_timeout=resolved_transport.open_timeout,
        ping_interval=resolved_transport.ping_interval,
        ping_timeout=resolved_transport.ping_timeout,
        close_timeout=resolved_transport.close_timeout,
        max_size=resolved_transport.max_size,
        max_queue=resolved_transport.max_queue,
    )


def _request_path(request: Request) -> str:
    parsed = urlsplit(request.path)
    return parsed.path or "/"


def _process_request(
    connection: ServerConnection,
    request: Request,
    *,
    server_options: ServerOptions,
    metadata: ServerMetadata,
) -> Response | None:
    del connection
    request_path = _request_path(request)

    if request_path == metadata.health_path:
        return _text_response(HTTPStatus.OK, "ok\n")
    if request_path == metadata.metadata_path:
        body = json.dumps(metadata.to_json_dict(), sort_keys=True).encode("utf-8")
        return _response(
            HTTPStatus.OK,
            body=body,
            content_type="application/json",
        )
    if request_path == metadata.websocket_path:
        if is_bearer_authorized(request.headers, server_options.bearer_token):
            return None
        return _response(
            HTTPStatus.UNAUTHORIZED,
            body=b"missing or invalid bearer token\n",
            content_type="text/plain; charset=utf-8",
            extra_headers=(("WWW-Authenticate", 'Bearer realm="acpremote"'),),
        )
    return _text_response(HTTPStatus.NOT_FOUND, "not found\n")


def _text_response(status: HTTPStatus, body: str) -> Response:
    return _response(
        status,
        body=body.encode("utf-8"),
        content_type="text/plain; charset=utf-8",
    )


def _response(
    status: HTTPStatus,
    *,
    body: bytes,
    content_type: str,
    extra_headers: tuple[tuple[str, str], ...] = (),
) -> Response:
    headers = Headers()
    headers["Content-Type"] = content_type
    headers["Content-Length"] = str(len(body))
    for header_name, header_value in extra_headers:
        headers[header_name] = header_value
    return Response(
        status_code=int(status),
        reason_phrase=status.phrase,
        headers=headers,
        body=body,
    )
