from __future__ import annotations as _annotations

from typing import TYPE_CHECKING, Generic, TypeVar

from acp.exceptions import RequestError
from acp.schema import (
    HttpMcpServer,
    McpServerStdio,
    NewSessionResponse,
    ResumeSessionResponse,
    SseMcpServer,
)

from ..session.state import AcpSessionContext, utc_now

if TYPE_CHECKING:
    from ._session_runtime import _SessionRuntime
    from .session_surface import SessionSurface

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)
OutputDataT = TypeVar("OutputDataT", covariant=True)

__all__ = ("_SessionLifecycle",)


class _SessionLifecycle(Generic[AgentDepsT, OutputDataT]):
    def __init__(self, runtime: _SessionRuntime[AgentDepsT, OutputDataT]) -> None:
        self._runtime = runtime

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None = None,
    ) -> NewSessionResponse:
        owner = self._runtime._owner
        session = self._runtime._bind_session_client(
            AcpSessionContext(
                session_id=owner._new_session_id(),
                cwd=self._runtime._normalize_cwd(cwd),
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        self._runtime._update_session_mcp_servers(session, mcp_servers)
        owner._config.session_store.save(session)
        surface = await self._prepare_session_surface(session, replay_transcript=False)
        return self._new_session_response(session, surface=surface)

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None = None,
    ) -> NewSessionResponse | None:
        session = self._runtime._owner._config.session_store.get(session_id)
        if session is None:
            return None
        session = self._runtime._bind_session_client(session)
        session.cwd = self._runtime._normalize_cwd(cwd)
        self._runtime._update_session_mcp_servers(session, mcp_servers)
        session.updated_at = utc_now()
        self._runtime._owner._config.session_store.save(session)
        surface = await self._prepare_session_surface(session, replay_transcript=True)
        return self._new_session_response(session, surface=surface)

    async def fork_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None = None,
    ) -> NewSessionResponse:
        owner = self._runtime._owner
        forked_session = owner._config.session_store.fork(
            session_id,
            new_session_id=owner._new_session_id(),
            cwd=self._runtime._normalize_cwd(cwd),
        )
        if forked_session is None:
            raise RequestError.invalid_params({"sessionId": session_id})
        forked_session = self._runtime._bind_session_client(forked_session)
        self._runtime._update_session_mcp_servers(forked_session, mcp_servers)
        surface = await self._prepare_session_surface(forked_session, replay_transcript=False)
        return self._new_session_response(forked_session, surface=surface)

    async def resume_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None = None,
    ) -> ResumeSessionResponse:
        session = self._runtime._require_session(session_id)
        session.cwd = self._runtime._normalize_cwd(cwd)
        self._runtime._update_session_mcp_servers(session, mcp_servers)
        session.updated_at = utc_now()
        self._runtime._owner._config.session_store.save(session)
        surface = await self._prepare_session_surface(session, replay_transcript=True)
        return ResumeSessionResponse(
            config_options=surface.config_options,
            models=surface.model_state,
            modes=surface.mode_state,
        )

    async def _prepare_session_surface(
        self,
        session: AcpSessionContext,
        *,
        replay_transcript: bool,
    ) -> SessionSurface:
        owner = self._runtime._owner
        if replay_transcript:
            await owner._replay_transcript(session)
        agent = await owner._agent_source.get_agent(session)
        self._runtime._configure_agent_runtime(session, agent)
        surface = await self._runtime._build_session_surface(session, agent)
        await self._runtime._emit_session_state_updates(
            session,
            surface,
            emit_available_commands=True,
            emit_config_options=False,
            emit_current_mode=False,
            emit_plan=True,
            emit_session_info=True,
        )
        return surface

    def _new_session_response(
        self,
        session: AcpSessionContext,
        *,
        surface: SessionSurface,
    ) -> NewSessionResponse:
        return NewSessionResponse(
            session_id=session.session_id,
            config_options=surface.config_options,
            models=surface.model_state,
            modes=surface.mode_state,
        )
