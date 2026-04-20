from __future__ import annotations as _annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeAlias, cast

from acp.client.connection import ClientSideConnection
from acp.helpers import text_block, tool_content
from acp.interfaces import Agent, Client
from acp.schema import (
    AudioContentBlock,
    AuthenticateResponse,
    ClientCapabilities,
    CloseSessionResponse,
    EmbeddedResourceContentBlock,
    FileSystemCapabilities,
    ForkSessionResponse,
    HttpMcpServer,
    ImageContentBlock,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    McpServerStdio,
    PromptResponse,
    ResourceContentBlock,
    ResumeSessionResponse,
    SetSessionConfigOptionResponse,
    SetSessionModelResponse,
    SetSessionModeResponse,
    SseMcpServer,
    TextContentBlock,
    ToolCallProgress,
)

from .client import RemoteClientConnection, connect_remote_agent
from .config import TransportOptions

__all__ = ("RemoteProxyAgent", "connect_acp")

_HeaderPairs = Mapping[str, str] | Sequence[tuple[str, str]]
SessionConfigOptionValue: TypeAlias = bool | str


@dataclass(slots=True)
class _PromptLatencyState:
    started_at: float
    sequence: int
    first_update_ms: int | None = None
    update_count: int = 0


@dataclass(slots=True, frozen=True)
class _PromptLatencySnapshot:
    tool_call_id: str
    total_ms: int
    first_update_ms: int | None
    update_count: int


@dataclass(slots=True)
class _TransportLatencyTracker:
    _active: dict[str, _PromptLatencyState] = field(default_factory=dict)
    _sequence: int = 0

    def start_prompt(self, session_id: str) -> None:
        self._sequence += 1
        self._active[session_id] = _PromptLatencyState(
            started_at=time.perf_counter(),
            sequence=self._sequence,
        )

    def update_meta(self, session_id: str) -> dict[str, Any] | None:
        state = self._active.get(session_id)
        if state is None:
            return None
        state.update_count += 1
        elapsed_ms = _elapsed_ms(state.started_at)
        if state.first_update_ms is None:
            state.first_update_ms = elapsed_ms
        return {
            "acpremote": {
                "transport_latency": {
                    "elapsed_ms": elapsed_ms,
                    "first_update_ms": state.first_update_ms,
                    "update_count": state.update_count,
                }
            }
        }

    def finish_prompt(self, session_id: str) -> _PromptLatencySnapshot | None:
        state = self._active.pop(session_id, None)
        if state is None:
            return None
        return _PromptLatencySnapshot(
            tool_call_id=f"acpremote:latency:{session_id}:{state.sequence}",
            total_ms=_elapsed_ms(state.started_at),
            first_update_ms=state.first_update_ms,
            update_count=state.update_count,
        )


@dataclass(slots=True)
class _LatencyClient:
    delegate: Client
    tracker: _TransportLatencyTracker
    emit_latency_meta: bool

    async def request_permission(
        self,
        options: list[Any],
        session_id: str,
        tool_call: Any,
        **kwargs: Any,
    ) -> Any:
        return await self.delegate.request_permission(
            options,
            session_id,
            tool_call,
            **_merge_field_meta(kwargs, self._meta(session_id)),
        )

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        await self.delegate.session_update(
            session_id,
            update,
            **_merge_field_meta(kwargs, self._meta(session_id)),
        )

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self.delegate.ext_method(method, params)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        await self.delegate.ext_notification(method, params)

    def _meta(self, session_id: str) -> dict[str, Any] | None:
        if not self.emit_latency_meta:
            return None
        return self.tracker.update_meta(session_id)


@dataclass(slots=True, kw_only=True)
class RemoteProxyAgent:
    url: str
    headers: _HeaderPairs | None = None
    bearer_token: str | None = None
    options: TransportOptions = field(default_factory=TransportOptions)
    _client: Client | None = field(default=None, init=False, repr=False)
    _remote: RemoteClientConnection | None = field(default=None, init=False, repr=False)
    _connect_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _latency_tracker: _TransportLatencyTracker = field(
        default_factory=_TransportLatencyTracker,
        init=False,
        repr=False,
    )
    _remote_cwd: str | None = field(default=None, init=False, repr=False)

    def on_connect(self, conn: Client) -> None:
        if self._client is not None and self._client is not conn and self._remote is not None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                loop.create_task(self._remote.close())
            self._remote = None
        self._client = conn

    async def close(self) -> None:
        if self._remote is None:
            return
        remote = self._remote
        self._remote = None
        self._remote_cwd = None
        await remote.close()

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: Any | None = None,
        client_info: Any | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        connection = await self._connection()
        return await connection.initialize(
            protocol_version=protocol_version,
            client_capabilities=_resolved_client_capabilities(
                client_capabilities,
                host_ownership=self.options.host_ownership,
            ),
            client_info=client_info,
            **kwargs,
        )

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> Any:
        connection = await self._connection()
        return await connection.new_session(
            cwd=self._resolve_cwd(cwd),
            mcp_servers=mcp_servers,
            **kwargs,
        )

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse | None:
        connection = await self._connection()
        return await connection.load_session(
            cwd=self._resolve_cwd(cwd),
            session_id=session_id,
            mcp_servers=mcp_servers,
            **kwargs,
        )

    async def list_sessions(
        self,
        cursor: str | None = None,
        cwd: str | None = None,
        **kwargs: Any,
    ) -> ListSessionsResponse:
        connection = await self._connection()
        return await connection.list_sessions(
            cursor=cursor,
            cwd=self._resolve_optional_cwd(cwd),
            **kwargs,
        )

    async def set_session_mode(
        self,
        mode_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> SetSessionModeResponse | None:
        connection = await self._connection()
        return await connection.set_session_mode(mode_id=mode_id, session_id=session_id, **kwargs)

    async def set_session_model(
        self,
        model_id: str,
        session_id: str,
        **kwargs: Any,
    ) -> SetSessionModelResponse | None:
        connection = await self._connection()
        return await connection.set_session_model(
            model_id=model_id,
            session_id=session_id,
            **kwargs,
        )

    async def set_config_option(
        self,
        config_id: str,
        session_id: str,
        value: SessionConfigOptionValue,
        **kwargs: Any,
    ) -> SetSessionConfigOptionResponse | None:
        connection = await self._connection()
        return await connection.set_config_option(
            config_id=config_id,
            session_id=session_id,
            value=value,
            **kwargs,
        )

    async def authenticate(
        self,
        method_id: str,
        **kwargs: Any,
    ) -> AuthenticateResponse | None:
        connection = await self._connection()
        return await connection.authenticate(method_id=method_id, **kwargs)

    async def prompt(
        self,
        prompt: list[
            TextContentBlock
            | ImageContentBlock
            | AudioContentBlock
            | ResourceContentBlock
            | EmbeddedResourceContentBlock
        ],
        session_id: str,
        message_id: str | None = None,
        **kwargs: Any,
    ) -> PromptResponse:
        self._latency_tracker.start_prompt(session_id)
        connection = await self._connection()
        response: PromptResponse
        try:
            response = await connection.prompt(
                prompt=prompt,
                session_id=session_id,
                message_id=message_id,
                **kwargs,
            )
        finally:
            snapshot = self._latency_tracker.finish_prompt(session_id)
            if snapshot is not None:
                await self._emit_latency_projection(session_id, snapshot)
        return response

    async def fork_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> ForkSessionResponse:
        connection = await self._connection()
        return await connection.fork_session(
            cwd=self._resolve_cwd(cwd),
            session_id=session_id,
            mcp_servers=mcp_servers,
            **kwargs,
        )

    async def resume_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        connection = await self._connection()
        return await connection.resume_session(
            cwd=self._resolve_cwd(cwd),
            session_id=session_id,
            mcp_servers=mcp_servers,
            **kwargs,
        )

    async def close_session(
        self,
        session_id: str,
        **kwargs: Any,
    ) -> CloseSessionResponse | None:
        connection = await self._connection()
        return await connection.close_session(session_id=session_id, **kwargs)

    async def cancel(
        self,
        session_id: str,
        **kwargs: Any,
    ) -> None:
        connection = await self._connection()
        await connection.cancel(session_id=session_id, **kwargs)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        connection = await self._connection()
        return await connection.ext_method(method=method, params=params)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        connection = await self._connection()
        await connection.ext_notification(method=method, params=params)

    async def _connection(self) -> ClientSideConnection:
        remote = await self._remote_connection()
        return remote.connection

    async def _remote_connection(self) -> RemoteClientConnection:
        if self._remote is not None:
            return self._remote
        async with self._connect_lock:
            if self._remote is not None:
                return self._remote
            client = self._client
            if client is None:
                raise RuntimeError("RemoteProxyAgent requires on_connect(...) before use.")
            remote_client: Client = client
            if self.options.emit_latency_meta:
                remote_client = cast(
                    Client,
                    _LatencyClient(
                        delegate=client,
                        tracker=self._latency_tracker,
                        emit_latency_meta=True,
                    ),
                )
            remote = await connect_remote_agent(
                remote_client,
                self.url,
                options=self.options,
                headers=self.headers,
                bearer_token=self.bearer_token,
            )
            self._remote_cwd = remote.metadata.remote_cwd if remote.metadata is not None else None
            self._remote = remote
            return remote

    def _resolve_cwd(self, cwd: str) -> str:
        return self._remote_cwd or cwd

    def _resolve_optional_cwd(self, cwd: str | None) -> str | None:
        if self._remote_cwd is not None:
            return self._remote_cwd
        return cwd

    async def _emit_latency_projection(
        self,
        session_id: str,
        snapshot: _PromptLatencySnapshot,
    ) -> None:
        if not self.options.emit_latency_projection:
            return
        client = self._client
        if client is None:
            return
        await client.session_update(
            session_id,
            ToolCallProgress(
                tool_call_id=snapshot.tool_call_id,
                session_update="tool_call_update",
                title="Transport Latency",
                kind="other",
                status="completed",
                content=[tool_content(text_block(_format_latency_summary(snapshot)))],
                raw_output={
                    "acpremote": {
                        "transport_latency": {
                            "total_ms": snapshot.total_ms,
                            "first_update_ms": snapshot.first_update_ms,
                            "update_count": snapshot.update_count,
                        }
                    }
                },
            ),
            source="acpremote-latency",
        )


def connect_acp(
    url: str,
    *,
    bearer_token: str | None = None,
    headers: _HeaderPairs | None = None,
    options: TransportOptions | None = None,
) -> Agent:
    return cast(
        Agent,
        RemoteProxyAgent(
            url=url,
            bearer_token=bearer_token,
            headers=headers,
            options=options or TransportOptions(),
        ),
    )


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))


def _merge_field_meta(
    kwargs: dict[str, Any],
    extra: dict[str, Any] | None,
) -> dict[str, Any]:
    if extra is None:
        return kwargs
    field_meta = kwargs.get("field_meta")
    if field_meta is None:
        return {**kwargs, "field_meta": extra}
    if not isinstance(field_meta, dict):
        return kwargs
    merged = dict(field_meta)
    for key, value in extra.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = {**current, **value}
        else:
            merged[key] = value
    return {**kwargs, "field_meta": merged}


def _format_latency_summary(snapshot: _PromptLatencySnapshot) -> str:
    first_update_text = (
        f"{snapshot.first_update_ms} ms"
        if snapshot.first_update_ms is not None
        else "no streamed updates"
    )
    return (
        f"First remote update: {first_update_text}\n"
        f"Turn complete: {snapshot.total_ms} ms\n"
        f"Streamed updates: {snapshot.update_count}"
    )


def _resolved_client_capabilities(
    client_capabilities: Any | None,
    *,
    host_ownership: str,
) -> Any | None:
    if host_ownership == "client_passthrough":
        return client_capabilities
    if not isinstance(client_capabilities, ClientCapabilities):
        return client_capabilities
    fs = client_capabilities.fs
    sanitized_fs = None
    if fs is not None:
        sanitized_fs = FileSystemCapabilities(
            field_meta=fs.field_meta,
            read_text_file=False,
            write_text_file=False,
        )
    return ClientCapabilities(
        field_meta=client_capabilities.field_meta,
        auth=client_capabilities.auth,
        fs=sanitized_fs,
        terminal=False,
    )
