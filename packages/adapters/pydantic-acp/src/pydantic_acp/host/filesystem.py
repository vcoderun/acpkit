from __future__ import annotations as _annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from acp.interfaces import Client as AcpClient
from acp.schema import ReadTextFileResponse, WriteTextFileResponse

from ..session.state import AcpSessionContext
from .policy import HostAccessPolicy

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
    access_policy: HostAccessPolicy | None = None
    workspace_root: Path | None = None

    async def read_text_file(
        self,
        path: str,
        *,
        limit: int | None = None,
        line: int | None = None,
    ) -> ReadTextFileResponse:
        self._enforce_path(path)
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
        self._enforce_path(path)
        return await self.client.write_text_file(
            content=content,
            path=path,
            session_id=self.session.session_id,
        )

    def _enforce_path(self, path: str) -> None:
        if self.access_policy is None:
            return
        self.access_policy.enforce_path(
            path,
            session_cwd=self.session.cwd,
            workspace_root=self.workspace_root,
        )
