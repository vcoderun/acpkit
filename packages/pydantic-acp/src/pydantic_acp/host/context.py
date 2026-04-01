from __future__ import annotations as _annotations

from dataclasses import dataclass

from acp.interfaces import Client as AcpClient

from ..session.state import AcpSessionContext
from .filesystem import ClientFilesystemBackend
from .terminal import ClientTerminalBackend

__all__ = ("ClientHostContext",)


@dataclass(slots=True, frozen=True, kw_only=True)
class ClientHostContext:
    client: AcpClient
    session: AcpSessionContext
    filesystem: ClientFilesystemBackend
    terminal: ClientTerminalBackend

    @classmethod
    def from_session(
        cls,
        *,
        client: AcpClient,
        session: AcpSessionContext,
    ) -> ClientHostContext:
        return cls(
            client=client,
            session=session,
            filesystem=ClientFilesystemBackend(client=client, session=session),
            terminal=ClientTerminalBackend(client=client, session=session),
        )
