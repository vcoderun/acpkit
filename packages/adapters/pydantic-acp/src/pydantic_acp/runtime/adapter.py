from __future__ import annotations as _annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from typing import Any, ClassVar, Generic, TypeVar, cast
from uuid import uuid4

from acp import PROTOCOL_VERSION
from acp.exceptions import RequestError
from acp.interfaces import Client as AcpClient
from acp.schema import (
    AcpMcpServer,
    AgentCapabilities,
    AuthenticateResponse,
    ClientCapabilities,
    CloseSessionResponse,
    ForkSessionResponse,
    HttpMcpServer,
    Implementation,
    InitializeResponse,
    McpServerStdio,
    PlanEntry,
    PromptCapabilities,
    SessionAdditionalDirectoriesCapabilities,
    SessionCapabilities,
    SessionCloseCapabilities,
    SessionForkCapabilities,
    SessionListCapabilities,
    SessionResumeCapabilities,
    SseMcpServer,
)
from pydantic_ai import Agent as PydanticAgent

from ..agent_source import AgentSource
from ..awaitables import resolve_value
from ..bridges import PrepareToolsBridge
from ..config import AdapterConfig
from ..extensions import (
    AuthenticationMethod,
    ExtensionContext,
    _filter_auth_methods_for_client,
    _validate_auth_methods,
)
from ..models import ModelOverride
from ..session.state import AcpSessionContext, JsonValue
from ._adapter_mixins import (
    _PromptRuntimeDelegationMixin,
    _SessionRuntimeDelegationMixin,
)
from ._adapter_prompt import _AdapterPromptHandler
from ._prompt_runtime import NativePlanGeneration, TaskPlan, _PromptRuntime
from ._session_runtime import _SessionRuntime
from .bridge_manager import BridgeManager
from .hook_introspection import list_agent_hooks

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)
OutputDataT = TypeVar("OutputDataT", covariant=True)

__all__ = ("NativePlanGeneration", "PydanticAcpAgent", "TaskPlan")


class PydanticAcpAgent(
    _PromptRuntimeDelegationMixin[AgentDepsT, OutputDataT],
    _SessionRuntimeDelegationMixin[AgentDepsT, OutputDataT],
    Generic[AgentDepsT, OutputDataT],
):
    """Expose a `pydantic_ai.Agent` as an ACP-compatible session runtime."""

    _pydantic_acp_meta_supported: ClassVar[bool] = True

    def __init__(
        self,
        agent_source: AgentSource[AgentDepsT, OutputDataT],
        *,
        config: AdapterConfig,
    ) -> None:
        self._agent_source = agent_source
        self._connection_factory_config = config
        bridge_projection_maps = tuple(
            projection_map
            for bridge in config.capability_bridges
            for projection_map in bridge.get_projection_maps()
        )
        self._config = (
            replace(
                config,
                projection_maps=(*config.projection_maps, *bridge_projection_maps),
            )
            if bridge_projection_maps
            else config
        )
        self._client: AcpClient | None = None
        self._client_capabilities: ClientCapabilities | None = None
        self._client_info: Implementation | None = None
        self._negotiated_protocol_version: int | None = None
        self._advertised_auth_method_ids: frozenset[str] = frozenset()
        self._bridge_manager = BridgeManager(
            base_classifier=self._config.tool_classifier,
            bridges=tuple(self._config.capability_bridges),
        )
        self._tool_classifier = self._bridge_manager.tool_classifier
        self._prompt_runtime = _PromptRuntime(self)
        self._adapter_prompt = _AdapterPromptHandler(self)
        self._session_runtime = _SessionRuntime(self)
        self._active_prompt_tasks: dict[str, asyncio.Task[Any]] = {}

    def on_connect(self, conn: AcpClient) -> None:
        self._client = conn

    def __call__(
        self,
        conn: AcpClient,
    ) -> PydanticAcpAgent[AgentDepsT, OutputDataT]:
        """Create an isolated adapter instance for one ACP SDK connection."""
        del conn
        return PydanticAcpAgent(
            self._agent_source,
            config=self._connection_factory_config,
        )

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
        del kwargs
        self._client_capabilities = client_capabilities
        negotiated_version = min(protocol_version, PROTOCOL_VERSION)
        self._client_info = client_info
        self._negotiated_protocol_version = negotiated_version
        self._advertised_auth_method_ids = frozenset()
        auth_methods: list[AuthenticationMethod] = []
        if self._config.authentication_provider is not None:
            contributed_auth_methods = await resolve_value(
                self._config.authentication_provider.get_auth_methods(client_capabilities),
            )
            auth_methods = _filter_auth_methods_for_client(
                _validate_auth_methods(contributed_auth_methods),
                client_capabilities,
            )
            self._advertised_auth_method_ids = frozenset(method.id for method in auth_methods)
        return InitializeResponse(
            protocol_version=negotiated_version,
            agent_capabilities=AgentCapabilities(
                load_session=True,
                mcp_capabilities=self._bridge_manager.get_mcp_capabilities(),
                prompt_capabilities=PromptCapabilities(
                    audio=self._config.prompt_capabilities.audio,
                    embedded_context=self._config.prompt_capabilities.embedded_context,
                    image=self._config.prompt_capabilities.image,
                ),
                session_capabilities=SessionCapabilities(
                    additional_directories=SessionAdditionalDirectoriesCapabilities(),
                    close=SessionCloseCapabilities(),
                    fork=SessionForkCapabilities(),
                    list=SessionListCapabilities(),
                    resume=SessionResumeCapabilities(),
                ),
            ),
            auth_methods=auth_methods,
            agent_info=Implementation(
                name=self._config.agent_name,
                title=self._config.agent_title,
                version=self._config.agent_version,
            ),
        )

    async def authenticate(
        self,
        method_id: str,
        **kwargs: Any,
    ) -> AuthenticateResponse | None:
        """Delegate ACP authentication or preserve the local no-op default."""
        del kwargs
        if self._config.authentication_provider is None:
            return None
        if method_id not in self._advertised_auth_method_ids:
            raise RequestError.invalid_params({"methodId": method_id})
        return await resolve_value(
            self._config.authentication_provider.authenticate(method_id),
        )

    async def fork_session(
        self,
        session_id: str,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer | AcpMcpServer]
        | None = None,
        **kwargs: Any,
    ) -> ForkSessionResponse:
        del kwargs
        response = await self._session_runtime.fork_session(
            session_id,
            cwd,
            additional_directories,
            mcp_servers,
        )
        return ForkSessionResponse(
            session_id=response.session_id,
            config_options=response.config_options,
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
        """Route configured ACP extension methods or reject unsupported methods."""
        contextual_router = self._config.contextual_extension_router
        if contextual_router is not None:
            return await resolve_value(
                contextual_router.handle_method(
                    self._extension_context(),
                    method,
                    cast("dict[str, JsonValue]", params),
                ),
            )
        if self._config.extension_router is None:
            raise RequestError.method_not_found(method)
        return await self._config.extension_router.handle_method(
            method,
            cast("dict[str, JsonValue]", params),
        )

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        """Route configured ACP extension notifications or ignore them by default."""
        contextual_router = self._config.contextual_extension_router
        if contextual_router is not None:
            await resolve_value(
                contextual_router.handle_notification(
                    self._extension_context(),
                    method,
                    cast("dict[str, JsonValue]", params),
                ),
            )
            return
        if self._config.extension_router is None:
            return
        await self._config.extension_router.handle_notification(
            method,
            cast("dict[str, JsonValue]", params),
        )

    def _extension_context(self) -> ExtensionContext:
        if self._client is None:
            raise RequestError.invalid_request({"reason": "client_not_connected"})
        if self._negotiated_protocol_version is None:
            raise RequestError.invalid_request({"reason": "connection_not_initialized"})
        return ExtensionContext(
            client=self._client,
            protocol_version=self._negotiated_protocol_version,
            client_capabilities=self._client_capabilities,
            client_info=self._client_info,
        )

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
        mcp_servers: list[HttpMcpServer | McpServerStdio | SseMcpServer | AcpMcpServer] | None,
    ) -> None:
        self._session_runtime._update_session_mcp_servers(session, mcp_servers)

    def _serialize_mcp_server(
        self,
        server: HttpMcpServer | McpServerStdio | SseMcpServer | AcpMcpServer,
    ) -> dict[str, JsonValue]:
        return self._session_runtime._serialize_mcp_server(server)

    def _require_session(self, session_id: str) -> AcpSessionContext:
        return self._session_runtime._require_session(session_id)
