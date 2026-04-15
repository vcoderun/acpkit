from __future__ import annotations as _annotations

from dataclasses import dataclass
from pathlib import Path

from acp.interfaces import Client as AcpClient

from ..session.state import AcpSessionContext
from .filesystem import ClientFilesystemBackend
from .policy import HostAccessPolicy
from .terminal import ClientTerminalBackend

__all__ = ("ClientHostContext",)


@dataclass(slots=True, frozen=True, kw_only=True)
class ClientHostContext:
    client: AcpClient
    session: AcpSessionContext
    filesystem: ClientFilesystemBackend
    terminal: ClientTerminalBackend
    access_policy: HostAccessPolicy | None = None
    workspace_root: Path | None = None

    @classmethod
    def from_session(
        cls,
        *,
        client: AcpClient,
        session: AcpSessionContext,
        access_policy: HostAccessPolicy | None = None,
        workspace_root: Path | None = None,
    ) -> ClientHostContext:
        return cls(
            client=client,
            session=session,
            filesystem=ClientFilesystemBackend(
                client=client,
                session=session,
                access_policy=access_policy,
                workspace_root=workspace_root,
            ),
            terminal=ClientTerminalBackend(
                client=client,
                session=session,
                access_policy=access_policy,
                workspace_root=workspace_root,
            ),
            access_policy=access_policy,
            workspace_root=workspace_root,
        )

    @classmethod
    def from_bound_session(
        cls,
        session: AcpSessionContext,
        *,
        access_policy: HostAccessPolicy | None = None,
        workspace_root: Path | None = None,
    ) -> ClientHostContext:
        client = session.client
        if client is None:
            raise ValueError("The ACP client is not connected to this session context.")
        return cls.from_session(
            client=client,
            session=session,
            access_policy=access_policy,
            workspace_root=workspace_root,
        )
