from __future__ import annotations as _annotations

from dataclasses import dataclass

from ._version import __version__
from .config import ServerOptions

__all__ = ("ServerMetadata", "TransportMetadata", "build_server_metadata")


@dataclass(frozen=True, kw_only=True)
class TransportMetadata:
    transport_kind: str = "websocket"
    transport_version: int = 1
    package_version: str = __version__


@dataclass(frozen=True, kw_only=True)
class ServerMetadata:
    transport_kind: str
    transport_version: int
    package_version: str
    auth_required: bool
    supported_auth_modes: tuple[str, ...]
    max_size: int
    max_queue: int
    compression: str | None
    health_path: str
    metadata_path: str
    websocket_path: str
    supported_agent_families: tuple[str, ...] = ()
    remote_cwd: str | None = None

    def to_json_dict(self) -> dict[str, object]:
        return {
            "transport_kind": self.transport_kind,
            "transport_version": self.transport_version,
            "package_version": self.package_version,
            "auth_required": self.auth_required,
            "supported_auth_modes": list(self.supported_auth_modes),
            "max_size": self.max_size,
            "max_queue": self.max_queue,
            "compression": self.compression,
            "health_path": self.health_path,
            "metadata_path": self.metadata_path,
            "websocket_path": self.websocket_path,
            "supported_agent_families": list(self.supported_agent_families),
            "remote_cwd": self.remote_cwd,
        }


def build_server_metadata(options: ServerOptions) -> ServerMetadata:
    transport_metadata = TransportMetadata()
    auth_required = options.bearer_token is not None and bool(options.bearer_token.strip())
    return ServerMetadata(
        transport_kind=transport_metadata.transport_kind,
        transport_version=transport_metadata.transport_version,
        package_version=transport_metadata.package_version,
        auth_required=auth_required,
        supported_auth_modes=("bearer",) if auth_required else (),
        max_size=options.transport.max_size,
        max_queue=options.transport.max_queue,
        compression=options.transport.compression,
        health_path=options.paths.health_path,
        metadata_path=options.paths.metadata_path,
        websocket_path=options.paths.websocket_path,
        supported_agent_families=options.supported_agent_families,
        remote_cwd=options.remote_cwd,
    )
