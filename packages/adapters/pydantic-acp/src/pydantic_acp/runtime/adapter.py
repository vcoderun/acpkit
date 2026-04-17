from __future__ import annotations as _annotations

import asyncio
from collections.abc import Sequence
from typing import Any, Generic, TypeVar
from uuid import uuid4

from acp import PROTOCOL_VERSION
from acp.exceptions import RequestError
from acp.interfaces import Client as AcpClient
from acp.schema import (
    AgentCapabilities,
    ClientCapabilities,
    CloseSessionResponse,
    ForkSessionResponse,
    HttpMcpServer,
    Implementation,
    InitializeResponse,
    McpServerStdio,
    PlanEntry,
    PromptCapabilities,
    SessionCapabilities,
    SessionCloseCapabilities,
    SessionForkCapabilities,
    SessionListCapabilities,
    SessionResumeCapabilities,
    SseMcpServer,
)
from pydantic_ai import Agent as PydanticAgent

from ..agent_source import AgentSource
from ..bridges import PrepareToolsBridge
from ..config import AdapterConfig
from ..models import ModelOverride
from ..session.state import AcpSessionContext, JsonValue
from ._adapter_mixins import _PromptRuntimeDelegationMixin, _SessionRuntimeDelegationMixin
from ._adapter_prompt import _AdapterPromptHandler
from ._prompt_runtime import NativePlanGeneration, TaskPlan, _PromptRuntime
from ._session_runtime import _SessionRuntime
from .bridge_manager import BridgeManager
from .hook_introspection import list_agent_hooks

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)
OutputDataT = TypeVar("OutputDataT", covariant=True)

__all__ = ("TaskPlan", "NativePlanGeneration", "PydanticAcpAgent")


class PydanticAcpAgent(
    _PromptRuntimeDelegationMixin[AgentDepsT, OutputDataT],
    _SessionRuntimeDelegationMixin[AgentDepsT, OutputDataT],
    Generic[AgentDepsT, OutputDataT],
):
    """Expose a `pydantic_ai.Agent` as an ACP-compatible session runtime."""

    def __init__(
        self,
        agent_source: AgentSource[AgentDepsT, OutputDataT],
        *,
        config: AdapterConfig,
    ) -> None:
        self._agent_source = agent_source
        self._config = config
        self._client: AcpClient | None = None
        self._bridge_manager = BridgeManager(
            base_classifier=config.tool_classifier,
            bridges=tuple(config.capability_bridges),
        )
        self._tool_classifier = self._bridge_manager.tool_classifier
        self._prompt_runtime = _PromptRuntime(self)
        self._adapter_prompt = _AdapterPromptHandler(self)
        self._session_runtime = _SessionRuntime(self)
        self._active_prompt_tasks: dict[str, asyncio.Task[Any]] = {}

    def on_connect(self, conn: AcpClient) -> None:
        self._client = conn

    def _new_session_id(self) -> str:
        return uuid4().hex

    def _list_agent_hooks(self, agent: PydanticAgent[AgentDepsT, OutputDataT]) -> list[Any]:
        return list_agent_hooks(agent)

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        """Negotiate ACP protocol version and advertise adapter capabilities."""
        del client_capabilities, client_info, kwargs
        negotiated_version = min(protocol_version, PROTOCOL_VERSION)
        return InitializeResponse(
            protocol_version=negotiated_version,
            agent_capabilities=AgentCapabilities(
                load_session=True,
                mcp_capabilities=self._bridge_manager.get_mcp_capabilities(),
                prompt_capabilities=PromptCapabilities(
                    audio=True,
                    embedded_context=True,
                    image=True,
                ),
                session_capabilities=SessionCapabilities(
                    close=SessionCloseCapabilities(),
                    fork=SessionForkCapabilities(),
                    list=SessionListCapabilities(),
                    resume=SessionResumeCapabilities(),
                ),
            ),
            agent_info=Implementation(
                name=self._config.agent_name,
                title=self._config.agent_title,
                version=self._config.agent_version,
            ),
        )

    async def authenticate(self, method_id: str, **kwargs: Any) -> None:
        """Accept ACP auth handshakes when the host does not require extra auth."""
        del method_id, kwargs
        return None

    async def fork_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None = None,
        **kwargs: Any,
    ) -> ForkSessionResponse:
        del kwargs
        response = await self._session_runtime.fork_session(cwd, session_id, mcp_servers)
        return ForkSessionResponse(
            session_id=response.session_id,
            config_options=response.config_options,
            models=response.models,
            modes=response.modes,
        )

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse | None:
        """Close a persisted ACP session if it exists."""
        del kwargs
        if not await self._session_runtime.close_session(session_id):
            return None
        return CloseSessionResponse()

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        """Cancel the active prompt task for a session, if one is running."""
        del kwargs
        active_task = self._active_prompt_tasks.get(session_id)
        if active_task is not None and not active_task.done():
            active_task.cancel()

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Reject unsupported ACP extension methods."""
        del params
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        """Ignore unsupported ACP extension notifications."""
        del method, params

    def _native_plan_bridge(
        self,
        session: AcpSessionContext,
    ) -> PrepareToolsBridge[Any] | None:
        return self._prompt_runtime._native_plan_bridge(session)

    def _supports_native_plan_state(self, session: AcpSessionContext) -> bool:
        return self._prompt_runtime._supports_native_plan_state(session)

    def _get_native_plan_entries(self, session: AcpSessionContext) -> list[PlanEntry] | None:
        return self._prompt_runtime._get_native_plan_entries(session)

    def _set_native_plan_state(
        self,
        session: AcpSessionContext,
        *,
        entries: Sequence[PlanEntry],
        plan_markdown: str | None,
    ) -> None:
        self._prompt_runtime._set_native_plan_state(
            session,
            entries=entries,
            plan_markdown=plan_markdown,
        )

    def _format_native_plan(self, session: AcpSessionContext) -> str:
        return self._prompt_runtime._format_native_plan(session)

    async def _persist_current_native_plan_state(
        self,
        session: AcpSessionContext,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        await self._prompt_runtime._persist_current_native_plan_state(
            session,
            agent=agent,
        )

    def _install_native_plan_tools(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        self._prompt_runtime._install_native_plan_tools(agent)

    def _consume_native_plan_update(
        self,
        session: AcpSessionContext,
    ) -> bool:
        return self._prompt_runtime._consume_native_plan_update(session)

    def _model_identity(self, model_value: ModelOverride | None) -> str | None:
        return self._session_runtime._model_identity(model_value)

    async def _handle_slash_command(
        self,
        command_name: str,
        *,
        argument: str | None,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> str | None:
        return await self._session_runtime._handle_slash_command(
            command_name,
            argument=argument,
            session=session,
            agent=agent,
        )

    def _apply_session_model_to_agent(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        session: AcpSessionContext,
    ) -> None:
        self._session_runtime._apply_session_model_to_agent(agent, session)

    def _set_agent_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        session: AcpSessionContext,
        model_id: str,
    ) -> None:
        self._session_runtime._set_agent_model(agent, session, model_id)

    def _restore_default_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        session: AcpSessionContext,
    ) -> bool:
        return self._session_runtime._restore_default_model(agent, session)

    def _restore_agent_default_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> bool:
        return self._session_runtime._restore_agent_default_model(agent)

    def _remember_default_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        self._session_runtime._remember_default_model(agent)

    def _resolve_selected_model(self, model_id: str) -> ModelOverride:
        return self._session_runtime._resolve_selected_model(model_id)

    def _update_session_mcp_servers(
        self,
        session: AcpSessionContext,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer] | None,
    ) -> None:
        self._session_runtime._update_session_mcp_servers(session, mcp_servers)

    def _serialize_mcp_server(
        self,
        server: HttpMcpServer | McpServerStdio | SseMcpServer,
    ) -> dict[str, JsonValue]:
        return self._session_runtime._serialize_mcp_server(server)

    def _require_session(self, session_id: str) -> AcpSessionContext:
        return self._session_runtime._require_session(session_id)
