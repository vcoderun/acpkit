from __future__ import annotations as _annotations

from dataclasses import dataclass
from typing import Protocol

from acp.interfaces import Client as AcpClient
from acp.schema import ReadTextFileResponse, WriteTextFileResponse

from ..session.state import AcpSessionContext

__all__ = (
    "ClientFilesystemBackend",
    "FilesystemBackend",
)


class FilesystemBackend(Protocol):
    async def read_text_file(
        self,
        path: str,
        *,
        limit: int | None = None,
        line: int | None = None,
    ) -> ReadTextFileResponse: ...

    async def write_text_file(
        self,
        path: str,
        content: str,
    ) -> WriteTextFileResponse | None: ...


@dataclass(slots=True, frozen=True, kw_only=True)
class ClientFilesystemBackend:
    client: AcpClient
    session: AcpSessionContext

    async def read_text_file(
        self,
        path: str,
        *,
        limit: int | None = None,
        line: int | None = None,
    ) -> ReadTextFileResponse:
        return await self.client.read_text_file(
            path=path,
            session_id=self.session.session_id,
            limit=limit,
            line=line,
        )

    async def write_text_file(
        self,
        path: str,
        content: str,
    ) -> WriteTextFileResponse | None:
        return await self.client.write_text_file(
            content=content,
            path=path,
            session_id=self.session.session_id,
        )
