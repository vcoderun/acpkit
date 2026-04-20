from __future__ import annotations as _annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPSConnection
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from acp import connect_to_agent
from acp.client.connection import ClientSideConnection
from acp.interfaces import Client
from websockets.asyncio.client import ClientConnection, connect

from .auth import bearer_headers
from .config import TransportOptions
from .metadata import ServerMetadata
from .stream import WebSocketStreamBridge, open_websocket_stream_bridge

__all__ = ("RemoteClientConnection", "connect_remote_agent", "fetch_server_metadata")

_HeaderPairs = Mapping[str, str] | Sequence[tuple[str, str]]


@dataclass(slots=True)
class RemoteClientConnection:
    connection: ClientSideConnection
    websocket: ClientConnection
    streams: WebSocketStreamBridge
    metadata: ServerMetadata | None = None

    async def close(self) -> None:
        await self.connection.close()
        await self.streams.close()


async def connect_remote_agent(
    client: Client,
    url: str,
    *,
    options: TransportOptions | None = None,
    headers: _HeaderPairs | None = None,
    bearer_token: str | None = None,
) -> RemoteClientConnection:
    resolved_options = options or TransportOptions()
    resolved_headers = _merge_headers(headers, bearer_headers(bearer_token))
    websocket = await connect(
        url,
        additional_headers=resolved_headers,
        compression=resolved_options.compression,
        open_timeout=resolved_options.open_timeout,
        ping_interval=resolved_options.ping_interval,
        ping_timeout=resolved_options.ping_timeout,
        close_timeout=resolved_options.close_timeout,
        max_size=resolved_options.max_size,
        max_queue=resolved_options.max_queue,
    )
    streams = await open_websocket_stream_bridge(
        websocket,
        reader_limit=resolved_options.reader_limit,
    )
    connection = connect_to_agent(client, streams.writer, streams.reader)
    metadata = await fetch_server_metadata(url, headers=resolved_headers)
    return RemoteClientConnection(
        connection=connection,
        websocket=websocket,
        streams=streams,
        metadata=metadata,
    )


async def fetch_server_metadata(
    url: str,
    *,
    headers: _HeaderPairs | None = None,
) -> ServerMetadata | None:
    metadata_url = _metadata_url(url)
    if metadata_url is None:
        return None
    return await asyncio.to_thread(_fetch_server_metadata_sync, metadata_url, headers)


def _merge_headers(
    base: _HeaderPairs | None,
    extra: dict[str, str] | None,
) -> _HeaderPairs | None:
    if not extra:
        return base
    if base is None:
        return extra
    if isinstance(base, Mapping):
        base_mapping = cast(Mapping[str, str], base)
        merged_items: list[tuple[str, str]] = [*base_mapping.items(), *extra.items()]
        return merged_items
    base_pairs = cast(Sequence[tuple[str, str]], base)
    merged_pairs: list[tuple[str, str]] = [*base_pairs, *extra.items()]
    return merged_pairs


def _metadata_url(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"ws", "wss"}:
        return None
    path = parsed.path or "/"
    if not path.endswith("/ws"):
        return None
    metadata_path = path.removesuffix("/ws") or "/"
    scheme = "https" if parsed.scheme == "wss" else "http"
    return urlunsplit((scheme, parsed.netloc, metadata_path, "", ""))


def _fetch_server_metadata_sync(
    url: str,
    headers: _HeaderPairs | None,
) -> ServerMetadata | None:
    parsed = urlsplit(url)
    if parsed.hostname is None:
        return None
    connection_class = HTTPSConnection if parsed.scheme == "https" else HTTPConnection
    connection = connection_class(parsed.hostname, parsed.port)
    try:
        connection.request("GET", parsed.path or "/", headers=_headers_dict(headers))
        response = connection.getresponse()
        if response.status != 200:
            return None
        payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    finally:
        connection.close()
    if not isinstance(payload, dict):
        return None
    try:
        return ServerMetadata(
            transport_kind=str(payload["transport_kind"]),
            transport_version=int(payload["transport_version"]),
            package_version=str(payload["package_version"]),
            auth_required=bool(payload["auth_required"]),
            supported_auth_modes=tuple(_string_list(payload.get("supported_auth_modes"))),
            max_size=int(payload["max_size"]),
            max_queue=int(payload["max_queue"]),
            compression=_optional_str(payload.get("compression")),
            health_path=str(payload["health_path"]),
            metadata_path=str(payload["metadata_path"]),
            websocket_path=str(payload["websocket_path"]),
            supported_agent_families=tuple(_string_list(payload.get("supported_agent_families"))),
            remote_cwd=_optional_str(payload.get("remote_cwd")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _headers_dict(headers: _HeaderPairs | None) -> dict[str, str]:
    if headers is None:
        return {}
    if isinstance(headers, Mapping):
        return dict(headers)
    return dict(headers)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)
