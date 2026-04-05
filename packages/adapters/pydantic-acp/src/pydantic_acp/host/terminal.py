from __future__ import annotations as _annotations

from dataclasses import dataclass
from typing import Protocol

from acp.interfaces import Client as AcpClient
from acp.schema import (
    CreateTerminalResponse,
    EnvVariable,
    KillTerminalResponse,
    ReleaseTerminalResponse,
    TerminalOutputResponse,
    WaitForTerminalExitResponse,
)

from ..session.state import AcpSessionContext

__all__ = (
    "ClientTerminalBackend",
    "TerminalBackend",
)


class TerminalBackend(Protocol):
    async def create_terminal(
        self,
        command: str,
        *,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: list[EnvVariable] | None = None,
        output_byte_limit: int | None = None,
    ) -> CreateTerminalResponse: ...

    async def terminal_output(self, terminal_id: str) -> TerminalOutputResponse: ...

    async def release_terminal(
        self,
        terminal_id: str,
    ) -> ReleaseTerminalResponse | None: ...

    async def wait_for_terminal_exit(
        self,
        terminal_id: str,
    ) -> WaitForTerminalExitResponse: ...

    async def kill_terminal(
        self,
        terminal_id: str,
    ) -> KillTerminalResponse | None: ...


@dataclass(slots=True, frozen=True, kw_only=True)
class ClientTerminalBackend:
    client: AcpClient
    session: AcpSessionContext

    async def create_terminal(
        self,
        command: str,
        *,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: list[EnvVariable] | None = None,
        output_byte_limit: int | None = None,
    ) -> CreateTerminalResponse:
        return await self.client.create_terminal(
            command=command,
            session_id=self.session.session_id,
            args=args,
            cwd=cwd,
            env=env,
            output_byte_limit=output_byte_limit,
        )

    async def terminal_output(self, terminal_id: str) -> TerminalOutputResponse:
        return await self.client.terminal_output(
            session_id=self.session.session_id,
            terminal_id=terminal_id,
        )

    async def release_terminal(
        self,
        terminal_id: str,
    ) -> ReleaseTerminalResponse | None:
        return await self.client.release_terminal(
            session_id=self.session.session_id,
            terminal_id=terminal_id,
        )

    async def wait_for_terminal_exit(
        self,
        terminal_id: str,
    ) -> WaitForTerminalExitResponse:
        return await self.client.wait_for_terminal_exit(
            session_id=self.session.session_id,
            terminal_id=terminal_id,
        )

    async def kill_terminal(
        self,
        terminal_id: str,
    ) -> KillTerminalResponse | None:
        return await self.client.kill_terminal(
            session_id=self.session.session_id,
            terminal_id=terminal_id,
        )
