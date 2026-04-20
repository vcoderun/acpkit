from __future__ import annotations as _annotations

from dataclasses import dataclass, field
from typing import Literal

from .limits import (
    DEFAULT_CLOSE_TIMEOUT,
    DEFAULT_MAX_MESSAGE_SIZE,
    DEFAULT_MAX_QUEUE,
    DEFAULT_OPEN_TIMEOUT,
    DEFAULT_PING_INTERVAL,
    DEFAULT_PING_TIMEOUT,
)

__all__ = (
    "DEFAULT_HEALTH_PATH",
    "ServerOptions",
    "ServerPaths",
    "TransportOptions",
    "build_server_paths",
    "normalize_mount_path",
)

DEFAULT_HEALTH_PATH = "/healthz"


@dataclass(frozen=True, kw_only=True)
class TransportOptions:
    max_size: int = DEFAULT_MAX_MESSAGE_SIZE
    reader_limit: int = DEFAULT_MAX_MESSAGE_SIZE
    max_queue: int = DEFAULT_MAX_QUEUE
    open_timeout: float = DEFAULT_OPEN_TIMEOUT
    ping_interval: float = DEFAULT_PING_INTERVAL
    ping_timeout: float = DEFAULT_PING_TIMEOUT
    close_timeout: float = DEFAULT_CLOSE_TIMEOUT
    compression: Literal["deflate"] | None = None
    host_ownership: Literal["remote", "client_passthrough"] = "remote"
    emit_latency_meta: bool = False
    emit_latency_projection: bool = False


@dataclass(frozen=True, kw_only=True)
class ServerPaths:
    metadata_path: str
    websocket_path: str
    health_path: str = DEFAULT_HEALTH_PATH


@dataclass(frozen=True, kw_only=True)
class ServerOptions:
    mount_path: str = "/acp"
    bearer_token: str | None = None
    supported_agent_families: tuple[str, ...] = ()
    remote_cwd: str | None = None
    transport: TransportOptions = field(default_factory=TransportOptions)

    @property
    def paths(self) -> ServerPaths:
        return build_server_paths(self.mount_path)


def normalize_mount_path(mount_path: str) -> str:
    stripped = mount_path.strip()
    if not stripped:
        raise ValueError("mount_path must not be empty")
    if not stripped.startswith("/"):
        stripped = f"/{stripped}"
    normalized = stripped.rstrip("/")
    return normalized or "/"


def build_server_paths(mount_path: str) -> ServerPaths:
    normalized_mount = normalize_mount_path(mount_path)
    websocket_path = f"{normalized_mount}/ws" if normalized_mount != "/" else "/ws"
    return ServerPaths(
        metadata_path=normalized_mount,
        websocket_path=websocket_path,
    )
