from __future__ import annotations as _annotations

import asyncio
import contextlib
import inspect
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from acp import connect_to_agent
from acp.client.connection import ClientSideConnection
from acp.interfaces import Client as AcpClient
from acp.schema import (
    AcpMcpServer,
    AuthenticateResponse,
    ClientCapabilities,
    CloseSessionResponse,
    ForkSessionResponse,
    HttpMcpServer,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    McpServerStdio,
    NewSessionResponse,
    PromptResponse,
    ResumeSessionResponse,
    SetSessionConfigOptionResponse,
    SetSessionModeResponse,
    SseMcpServer,
)

from .types import AgentPromptBlock

CommandStderrMode = Literal["inherit", "discard"]
McpServer = HttpMcpServer | SseMcpServer | AcpMcpServer | McpServerStdio

_DEFAULT_READER_LIMIT = 1024 * 1024

__all__ = ("AcpCommandAgent", "AcpCommandOptions", "CommandStderrMode")


@dataclass(frozen=True, kw_only=True)
class AcpCommandOptions:
    """Configuration for an ACP stdio command used as an agent backend."""

    command: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str] | None = None
    stderr_mode: CommandStderrMode = "inherit"
    terminate_timeout: float = 5.0

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("acp_command must contain at least one executable argument.")
        if self.stderr_mode not in ("inherit", "discard"):
            raise ValueError("stderr_mode must be either 'inherit' or 'discard'.")
        if not math.isfinite(self.terminate_timeout) or self.terminate_timeout <= 0:
            raise ValueError("terminate_timeout must be a positive finite number.")


@dataclass(kw_only=True)
class AcpCommandAgent:
    """ACP agent facade that delegates requests to a local stdio ACP command."""

    options: AcpCommandOptions
    _client: AcpClient | None = field(default=None, init=False, repr=False)
    _connection: ClientSideConnection | None = field(default=None, init=False, repr=False)
    _process: asyncio.subprocess.Process | None = field(default=None, init=False, repr=False)
    _connect_lock: asyncio.Lock | None = field(default=None, init=False, repr=False)
    _connect_lock_loop: asyncio.AbstractEventLoop | None = field(
        default=None, init=False, repr=False
    )
    _closed: bool = field(default=False, init=False, repr=False)

    def on_connect(self, conn: AcpClient) -> None:
        self._client = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        return await (await self._ensure_connection()).initialize(
            protocol_version=protocol_version,
            client_capabilities=client_capabilities,
            client_info=client_info,
            **kwargs,
        )

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[McpServer] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        return await (await self._ensure_connection()).new_session(
            cwd=cwd,
            additional_directories=additional_directories,
            mcp_servers=mcp_servers,
            **kwargs,
        )

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[McpServer] | None = None,
        additional_directories: list[str] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse | None:
        return await (await self._ensure_connection()).load_session(
            cwd=cwd,
            session_id=session_id,
            mcp_servers=mcp_servers,
            additional_directories=additional_directories,
            **kwargs,
        )

    async def list_sessions(
        self,
        cwd: str | None = None,
        cursor: str | None = None,
        **kwargs: Any,
    ) -> ListSessionsResponse:
        return await (await self._ensure_connection()).list_sessions(
            cursor=cursor, cwd=cwd, **kwargs
        )

    async def set_session_mode(
        self,
        session_id: str,
        mode_id: str,
        **kwargs: Any,
    ) -> SetSessionModeResponse | None:
        return await (await self._ensure_connection()).set_session_mode(
            mode_id=mode_id,
            session_id=session_id,
            **kwargs,
        )

    async def set_config_option(
        self,
        config_id: str,
        session_id: str,
        value: str | bool,
        **kwargs: Any,
    ) -> SetSessionConfigOptionResponse | None:
        return await (await self._ensure_connection()).set_config_option(
            config_id=config_id,
            session_id=session_id,
            value=value,
            **kwargs,
        )

    async def set_session_model(
        self,
        model_id: str,
        session_id: str,
    ) -> SetSessionConfigOptionResponse | None:
        """Compatibility helper that maps model selection to ACP config options."""
        return await self.set_config_option("model", session_id, model_id)

    async def authenticate(self, method_id: str, **kwargs: Any) -> AuthenticateResponse | None:
        return await (await self._ensure_connection()).authenticate(method_id=method_id, **kwargs)

    async def prompt(
        self,
        session_id: str,
        prompt: list[AgentPromptBlock],
        **kwargs: Any,
    ) -> PromptResponse:
        return await (await self._ensure_connection()).prompt(
            session_id=session_id,
            prompt=prompt,
            **kwargs,
        )

    async def fork_session(
        self,
        session_id: str,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[McpServer] | None = None,
        **kwargs: Any,
    ) -> ForkSessionResponse:
        return await (await self._ensure_connection()).fork_session(
            cwd=cwd,
            session_id=session_id,
            additional_directories=additional_directories,
            mcp_servers=mcp_servers,
            **kwargs,
        )

    async def resume_session(
        self,
        session_id: str,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[McpServer] | None = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        return await (await self._ensure_connection()).resume_session(
            cwd=cwd,
            session_id=session_id,
            additional_directories=additional_directories,
            mcp_servers=mcp_servers,
            **kwargs,
        )

    async def close_session(
        self,
        session_id: str,
        **kwargs: Any,
    ) -> CloseSessionResponse | None:
        return await (await self._ensure_connection()).close_session(
            session_id=session_id, **kwargs
        )

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        await (await self._ensure_connection()).cancel(session_id=session_id, **kwargs)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return await (await self._ensure_connection()).ext_method(method=method, params=params)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        await (await self._ensure_connection()).ext_notification(method=method, params=params)

    async def close(self) -> None:
        self._closed = True
        await self._close_current_connection()

    async def _ensure_connection(self) -> ClientSideConnection:
        if self._closed:
            raise RuntimeError("AcpCommandAgent is closed.")
        process = self._process
        connection = self._connection
        if process is not None and process.returncode is None and connection is not None:
            return connection

        async with self._get_connect_lock():
            process = self._process
            connection = self._connection
            if process is not None and process.returncode is None and connection is not None:
                return connection
            await self._close_current_connection()
            return await self._open_connection()

    async def _open_connection(self) -> ClientSideConnection:
        client = self._client
        if client is None:
            raise RuntimeError("AcpCommandAgent requires on_connect() before the first ACP call.")

        process = await _create_command_process(self.options)
        if process.stdin is None or process.stdout is None:
            await _terminate_process(process, timeout=self.options.terminate_timeout)
            raise RuntimeError("ACP command did not expose stdio pipes.")

        try:
            connection = connect_to_agent(
                client,
                process.stdin,
                process.stdout,
            )
        except BaseException:
            await _terminate_process(process, timeout=self.options.terminate_timeout)
            raise

        self._process = process
        self._connection = connection
        return connection

    async def _close_current_connection(self) -> None:
        connection = self._connection
        process = self._process
        self._connection = None
        self._process = None

        if connection is not None:
            with contextlib.suppress(Exception):
                close_result = connection.close()
                if inspect.isawaitable(close_result):
                    await close_result
        if process is not None:
            await _terminate_process(process, timeout=self.options.terminate_timeout)

    def _get_connect_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._connect_lock is None or self._connect_lock_loop is not loop:
            self._connect_lock = asyncio.Lock()
            self._connect_lock_loop = loop
        return self._connect_lock


async def _create_command_process(options: AcpCommandOptions) -> asyncio.subprocess.Process:
    stderr = None if options.stderr_mode == "inherit" else asyncio.subprocess.DEVNULL
    return await asyncio.create_subprocess_exec(
        *options.command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=stderr,
        cwd=options.cwd,
        env=_build_process_env(options.env),
        limit=_DEFAULT_READER_LIMIT,
    )


def _build_process_env(overrides: Mapping[str, str] | None) -> dict[str, str] | None:
    if overrides is None:
        return None
    process_env = dict(os.environ)
    process_env.update(overrides)
    return process_env


async def _terminate_process(
    process: asyncio.subprocess.Process,
    *,
    timeout: float,
) -> None:
    if process.returncode is not None:
        return

    stdin = process.stdin
    if stdin is not None:
        with contextlib.suppress(Exception):
            stdin.close()
            await stdin.wait_closed()

    if process.returncode is not None:
        return

    with contextlib.suppress(ProcessLookupError):
        process.terminate()

    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
        return
    except TimeoutError:
        pass
    except ProcessLookupError:
        return

    if process.returncode is not None:
        return

    with contextlib.suppress(ProcessLookupError):
        process.kill()
    with contextlib.suppress(ProcessLookupError, TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=timeout)
