from __future__ import annotations as _annotations

from ._version import __version__
from .client import RemoteClientConnection, connect_remote_agent
from .command import CommandOptions, run_remote_command_connection
from .config import (
    DEFAULT_HEALTH_PATH,
    ServerOptions,
    ServerPaths,
    TransportOptions,
    build_server_paths,
    normalize_mount_path,
)
from .metadata import ServerMetadata, TransportMetadata, build_server_metadata
from .proxy_agent import RemoteProxyAgent, connect_acp
from .server import (
    run_remote_agent_connection,
    serve_acp,
    serve_command,
    serve_remote_agent,
    serve_stdio_command,
)
from .stream import WebSocketStreamBridge, open_websocket_stream_bridge

__all__ = (
    "__version__",
    "CommandOptions",
    "DEFAULT_HEALTH_PATH",
    "RemoteClientConnection",
    "RemoteProxyAgent",
    "ServerMetadata",
    "ServerOptions",
    "ServerPaths",
    "TransportOptions",
    "TransportMetadata",
    "WebSocketStreamBridge",
    "build_server_metadata",
    "build_server_paths",
    "connect_remote_agent",
    "connect_acp",
    "normalize_mount_path",
    "open_websocket_stream_bridge",
    "run_remote_command_connection",
    "run_remote_agent_connection",
    "serve_acp",
    "serve_command",
    "serve_remote_agent",
    "serve_stdio_command",
)
