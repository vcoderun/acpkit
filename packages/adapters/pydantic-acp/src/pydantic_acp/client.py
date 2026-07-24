from __future__ import annotations as _annotations

import asyncio
import inspect
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypeAlias, cast
from uuid import uuid4

from acp import PROTOCOL_VERSION
from acp.exceptions import RequestError
from acp.helpers import text_block
from acp.interfaces import Agent as AcpAgent
from acp.interfaces import Client as AcpClient
from acp.schema import (
    AgentMessageChunk,
    AuthMethodAgent,
    ClientCapabilities,
    ClientSessionCapabilities,
    CreateElicitationResponse,
    CreateTerminalResponse,
    ElicitationMode,
    EnvVarAuthMethod,
    EnvVariable,
    Implementation,
    KillTerminalResponse,
    NewSessionResponse,
    PermissionOption,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    RequestPermissionResponse,
    SessionConfigOptionsCapabilities,
    SessionConfigOptionSelect,
    TerminalAuthMethod,
    TerminalOutputResponse,
    ToolCallUpdate,
    UsageUpdate,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)
from pydantic_ai.exceptions import UnexpectedModelBehavior, UserError
from pydantic_ai.messages import (
    AudioUrl,
    BinaryContent,
    DocumentUrl,
    FinishReason,
    ImageUrl,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelResponseStreamEvent,
    RetryPromptPart,
    SystemPromptPart,
    TextContent,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UploadedFile,
    UserContent,
    UserPromptPart,
    VideoUrl,
)
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.native_tools import AbstractNativeTool
from pydantic_ai.profiles import ModelProfile, ModelProfileSpec
from pydantic_ai.providers import Provider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage
from typing_extensions import TypeIs

from ._meta_protocol import (
    MISSING_STRUCTURED_OUTPUT,
    build_structured_output_request_meta,
    extract_field_meta,
    extract_structured_output,
)
from ._version import __version__
from .types import AgentPromptBlock

HistoryMode: TypeAlias = Literal["latest_user", "full"]
AuthMethod: TypeAlias = EnvVarAuthMethod | TerminalAuthMethod | AuthMethodAgent
_DEFAULT_MODEL_NAME = "agent"
_TASK_GROUP_ERROR_MESSAGE = "unhandled errors in a TaskGroup"
_AUTH_REQUIRED_DIAGNOSTIC = (
    "The ACP agent requires authentication (session/new returned auth_required / -32000)"
)

AcpPromptRenderer: TypeAlias = Callable[
    [Sequence[ModelMessage], ModelRequestParameters],
    Sequence[AgentPromptBlock] | Awaitable[Sequence[AgentPromptBlock]],
]

ACP_MODEL_PROFILE: ModelProfile = ModelProfile(
    supports_tools=False,
    supports_json_schema_output=False,
    supports_json_object_output=False,
    supports_image_output=False,
    supported_native_tools=frozenset[type[AbstractNativeTool]](),
)
_ACP_META_MODEL_PROFILE: ModelProfile = ModelProfile(
    supports_tools=True,
    supports_json_schema_output=False,
    supports_json_object_output=False,
    supports_image_output=False,
    supported_native_tools=frozenset[type[AbstractNativeTool]](),
)

__all__ = (
    "ACP_MODEL_PROFILE",
    "AcpHostBridge",
    "AcpModel",
    "AcpPromptRenderer",
    "AcpProvider",
    "AcpUpdateRecord",
)


# ---------------------------------------------------------------------------
# AcpUpdateRecord
# ---------------------------------------------------------------------------


class AcpUpdateRecord:
    """A host-side ACP update observed while an ACP-backed model request runs."""

    __slots__ = ("session_id", "source", "update")

    def __init__(self, *, session_id: str, update: Any, source: str | None = None) -> None:
        self.session_id = session_id
        self.update = update
        self.source = source


# ---------------------------------------------------------------------------
# AcpHostBridge
# ---------------------------------------------------------------------------


class AcpHostBridge:
    """Minimal ACP host/client implementation used by :class:`AcpProvider`.

    ACP agents send their visible output to a connected ACP client via
    ``session_update``. Pydantic AI models, however, return a ``ModelResponse``.
    This bridge is the seam between the two contracts: it records ACP updates so
    ``AcpModel`` can fold agent message chunks back into Pydantic AI response
    parts, while optionally delegating real host operations to an upstream ACP
    client supplied by the caller.

    The bridge intentionally does not emulate a filesystem, terminal, approval
    UI, or extension namespace. When an ACP agent asks for such host operations
    and no delegate was supplied, the request fails explicitly instead of
    inventing host behavior that is not present.
    """

    def __init__(self, *, delegate: AcpClient | None = None) -> None:
        self.delegate = delegate
        self.updates: list[AcpUpdateRecord] = []

    def snapshot_index(self) -> int:
        """Return an index that can later be used to read only new updates."""
        return len(self.updates)

    def records_since(
        self,
        index: int,
        *,
        session_id: str | None = None,
    ) -> list[AcpUpdateRecord]:
        """Return recorded updates after ``index``, optionally scoped to a session."""
        records = self.updates[index:]
        if session_id is None:
            return list(records)
        return [record for record in records if record.session_id == session_id]

    def agent_message_text_since(self, index: int, *, session_id: str) -> str:
        """Concatenate ACP agent message chunks recorded after ``index``."""
        text_parts: list[str] = []
        for record in self.records_since(index, session_id=session_id):
            update = record.update
            if not _is_agent_message_chunk(update):
                continue
            content = getattr(update, "content", None)
            text = getattr(content, "text", None)
            if isinstance(text, str):
                text_parts.append(text)
        return "".join(text_parts)

    def usage_update_since(self, index: int, *, session_id: str) -> RequestUsage:
        """Collect the latest ACP usage update observed after ``index``."""
        usage = RequestUsage()
        for record in self.records_since(index, session_id=session_id):
            update = record.update
            if not isinstance(update, UsageUpdate):
                continue
            usage = _usage_from_acp(getattr(update, "usage", None))
        return usage

    async def session_update(
        self,
        session_id: str,
        update: Any,
        **kwargs: Any,
    ) -> None:
        """Record an ACP update and optionally forward it to a real host client."""
        self.updates.append(
            AcpUpdateRecord(
                session_id=session_id,
                update=update,
                source=kwargs.get("source"),
            ),
        )
        if self.delegate is not None and hasattr(self.delegate, "session_update"):
            await self._call_delegate(
                "session_update",
                session_id=session_id,
                update=update,
                **kwargs,
            )

    async def request_permission(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        return await self._call_delegate(
            "request_permission",
            options=options,
            session_id=session_id,
            tool_call=tool_call,
            **kwargs,
        )

    async def write_text_file(
        self,
        session_id: str,
        path: str,
        content: str,
        **kwargs: Any,
    ) -> WriteTextFileResponse | None:
        return await self._call_delegate(
            "write_text_file",
            content=content,
            path=path,
            session_id=session_id,
            **kwargs,
        )

    async def read_text_file(
        self,
        session_id: str,
        path: str,
        line: int | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> ReadTextFileResponse:
        return await self._call_delegate(
            "read_text_file",
            path=path,
            session_id=session_id,
            limit=limit,
            line=line,
            **kwargs,
        )

    async def create_terminal(
        self,
        session_id: str,
        command: str,
        args: list[str] | None = None,
        env: list[EnvVariable] | None = None,
        cwd: str | None = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> CreateTerminalResponse:
        return await self._call_delegate(
            "create_terminal",
            command=command,
            session_id=session_id,
            args=args,
            cwd=cwd,
            env=env,
            output_byte_limit=output_byte_limit,
            **kwargs,
        )

    async def terminal_output(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> TerminalOutputResponse:
        return await self._call_delegate(
            "terminal_output",
            session_id=session_id,
            terminal_id=terminal_id,
            **kwargs,
        )

    async def release_terminal(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> ReleaseTerminalResponse | None:
        return await self._call_delegate(
            "release_terminal",
            session_id=session_id,
            terminal_id=terminal_id,
            **kwargs,
        )

    async def wait_for_terminal_exit(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> WaitForTerminalExitResponse:
        return await self._call_delegate(
            "wait_for_terminal_exit",
            session_id=session_id,
            terminal_id=terminal_id,
            **kwargs,
        )

    async def kill_terminal(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> KillTerminalResponse | None:
        return await self._call_delegate(
            "kill_terminal",
            session_id=session_id,
            terminal_id=terminal_id,
            **kwargs,
        )

    async def create_elicitation(
        self,
        message: str,
        mode: ElicitationMode,
        **kwargs: Any,
    ) -> CreateElicitationResponse:
        return await self._call_delegate(
            "create_elicitation",
            message=message,
            mode=mode,
            **kwargs,
        )

    async def complete_elicitation(self, elicitation_id: str, **kwargs: Any) -> None:
        await self._call_delegate(
            "complete_elicitation",
            elicitation_id=elicitation_id,
            **kwargs,
        )

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self._call_delegate("ext_method", method=method, params=params)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        if self.delegate is None or not hasattr(self.delegate, "ext_notification"):
            return
        await self._call_delegate("ext_notification", method=method, params=params)

    def on_connect(self, conn: AcpAgent) -> None:
        """Forward reverse connections to a delegate host client when it supports them."""
        if self.delegate is not None and hasattr(self.delegate, "on_connect"):
            self.delegate.on_connect(conn)

    async def _call_delegate(self, method_name: str, **kwargs: Any) -> Any:
        delegate = self.delegate
        method = getattr(delegate, method_name, None) if delegate is not None else None
        if method is None:
            raise RuntimeError(
                f"ACP agent requested host method {method_name!r}, but no host client delegate "
                "was supplied to AcpProvider.",
            )
        result = method(**kwargs)
        if inspect.isawaitable(result):
            return await result
        return result


# ---------------------------------------------------------------------------
# Internal result type
# ---------------------------------------------------------------------------


class _AcpPromptResult:
    __slots__ = ("session_id", "stop_reason", "structured_output", "text", "usage")

    def __init__(
        self,
        *,
        text: str,
        usage: RequestUsage,
        stop_reason: str | None,
        session_id: str,
        structured_output: Any = MISSING_STRUCTURED_OUTPUT,
    ) -> None:
        self.text = text
        self.usage = usage
        self.stop_reason = stop_reason
        self.session_id = session_id
        self.structured_output = structured_output


def _default_client_capabilities() -> ClientCapabilities:
    return ClientCapabilities(
        session=ClientSessionCapabilities(
            config_options=SessionConfigOptionsCapabilities(),
        ),
    )


def _is_task_group_error(exc: BaseException) -> TypeIs[ExceptionGroup[Exception]]:
    return isinstance(exc, ExceptionGroup) and exc.message == _TASK_GROUP_ERROR_MESSAGE


def _unwrap_acp_error(exc: Exception) -> Exception:
    """Return the ACP agent's real error, free of anyio TaskGroup wrapping.

    ACP calls run over a stdio connection whose background reader lives in an
    anyio task group. An agent/protocol error (rate limit, auth rejection,
    upstream API failure) can therefore reach the caller wrapped as a
    single-child :class:`BaseExceptionGroup` ("unhandled errors in a
    TaskGroup"), or as the real error carrying that group as its
    ``__context__``. Both bury the actual cause. This peels single-child groups
    down to the leaf and drops a TaskGroup ``__context__`` so the meaningful
    error propagates on its own instead of as an opaque group. Other exception
    groups are preserved because they may carry meaningful aggregate failures.
    """
    leaf: Exception = exc
    while (
        _is_task_group_error(leaf)
        and len(leaf.exceptions) == 1
        and isinstance(leaf.exceptions[0], Exception)
    ):
        leaf = leaf.exceptions[0]
    if leaf.__context__ is not None and _is_task_group_error(leaf.__context__):
        leaf.__context__ = None
        leaf.__suppress_context__ = True
    return leaf


# ---------------------------------------------------------------------------
# AcpProvider
# ---------------------------------------------------------------------------


class AcpProvider(Provider[AcpAgent]):
    """Pydantic AI provider that treats an ACP agent as the model backend.

    This is the inverse of the normal ``pydantic-acp`` server adapter. The
    server adapter exposes a ``pydantic_ai.Agent`` through ACP. ``AcpProvider``
    consumes an existing ACP agent and makes it available to Pydantic AI as a
    provider/model pair, so application code can write ordinary Pydantic AI
    agents while delegating the underlying model turn to ACP.

    The provider owns ACP protocol/session setup, model selection through the
    remote agent's ``model`` config option, host/client update
    capture, and prompt rendering. It deliberately remains a provider rather
    than an alternate agent framework: Pydantic AI still owns the outer agent
    run, result validation, usage accumulation, and history shape.
    """

    _history_mode: HistoryMode

    def __init__(
        self,
        *,
        acp_agent: AcpAgent,
        host_client: AcpClient | None = None,
        cwd: str | Path = ".",
        name: str = "acp",
        base_url: str = "acp://local",
        protocol_version: int = PROTOCOL_VERSION,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        mcp_servers: Sequence[Any] | None = None,
        prompt_renderer: AcpPromptRenderer | None = None,
        history_mode: HistoryMode = "latest_user",
        enable_pydantic_acp_meta: bool | None = None,
        auth_method_id: str | None = None,
        raise_on_empty_turn: bool = False,
    ) -> None:
        """Create a new ACP provider.

        Args:
            acp_agent: The ACP agent to wrap as a Pydantic AI provider/model.
            host_client: Optional upstream :class:`AcpClient` to delegate real
                host operations to (file I/O, terminals, permissions, etc.).
            cwd: Working directory passed to the ACP session on creation.
            name: Provider name reported via :attr:`name`. Defaults to
                ``"acp"``.
            base_url: Base URL reported via :attr:`base_url`. Defaults to
                ``"acp://local"``.
            protocol_version: ACP protocol version used during
                ``initialize``. Defaults to :data:`acp.PROTOCOL_VERSION`.
            client_capabilities: ACP client capabilities sent during
                ``initialize``. Defaults to an empty
                :class:`~acp.schema.ClientCapabilities`.
            client_info: ACP implementation info sent during ``initialize``.
                Defaults to a ``pydantic-acp-client`` stub.
            mcp_servers: Optional list of MCP servers forwarded to
                ``new_session``.
            prompt_renderer: Custom callable that converts Pydantic AI
                messages into ACP prompt blocks. When ``None`` the built-in
                renderer is used.
            history_mode: Controls how previous messages are rendered into
                the ACP prompt. ``"latest_user"`` (default) sends only the
                latest user turn; ``"full"`` sends the entire conversation.
            enable_pydantic_acp_meta: Enables the private ``pydantic_acp``
                ``_meta`` extension used for ACP-backed Pydantic AI structured
                output. ``None`` auto-enables it only for ACP agents produced
                by this package; arbitrary ACP agents must not be trusted to
                implement this private contract.
            auth_method_id: ACP ``authenticate`` method id to use when the
                agent rejects ``session/new`` with an ``auth_required``
                (``-32000``) error. When ``None`` the provider falls back to
                the first authentication method advertised by the agent's
                ``initialize`` response.
            raise_on_empty_turn: When ``True``, a prompt turn that produces no
                visible text for a text-output request raises
                :class:`~pydantic_ai.exceptions.UnexpectedModelBehavior` with an
                ACP-specific diagnostic instead of returning an empty response.
                Defaults to ``False`` to preserve the standard contract where an
                empty ACP turn yields a response with no parts.

        """
        self._client = acp_agent

        self._host = AcpHostBridge(delegate=host_client)
        self._name = name
        self._base_url = base_url
        self._cwd = str(cwd)
        self._protocol_version = protocol_version
        self._client_capabilities = client_capabilities
        self._client_info = client_info or Implementation(
            name="pydantic-acp-client",
            version=__version__,
        )
        self._mcp_servers = list(mcp_servers or [])
        self._prompt_renderer = prompt_renderer
        self._history_mode = history_mode
        self._enable_pydantic_acp_meta = (
            _agent_supports_pydantic_acp_meta(acp_agent)
            if enable_pydantic_acp_meta is None
            else enable_pydantic_acp_meta
        )
        self._auth_method_id = auth_method_id
        self._raise_on_empty_turn = raise_on_empty_turn
        self._initialized = False
        self._session_id: str | None = None
        self._current_model_name: str | None = None
        self._model_config_option_available: bool | None = None
        self._auth_methods: list[AuthMethod] = []
        self._authenticated = False
        self._session_lock: asyncio.Lock | None = None
        self._session_lock_loop: asyncio.AbstractEventLoop | None = None

        if hasattr(self._client, "on_connect"):
            self._client.on_connect(self._host)

    # -- Provider abstract interface ----------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def client(self) -> AcpAgent:
        return self._client

    @staticmethod
    def model_profile(model_name: str) -> ModelProfile | None:
        del model_name
        return ACP_MODEL_PROFILE

    # -- Additional public API ----------------------------------------------

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def host(self) -> AcpHostBridge:
        """The ACP host bridge connected to the wrapped ACP agent."""
        return self._host

    @property
    def updates(self) -> list[AcpUpdateRecord]:
        """All ACP updates recorded by the host bridge so far."""
        return list(self._host.updates)

    @property
    def enable_pydantic_acp_meta(self) -> bool:
        """Whether this provider opts into private ``pydantic_acp`` ACP metadata."""
        return self._enable_pydantic_acp_meta

    @property
    def raise_on_empty_turn(self) -> bool:
        """Whether an empty ACP turn raises instead of returning empty parts."""
        return self._raise_on_empty_turn

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> bool | None:
        result = await super().__aexit__(exc_type, exc_val, exc_tb)
        if self._entered_count == 0:
            await self.close()
        return result

    async def close(self) -> None:
        """Close the wrapped ACP agent when it exposes an async-compatible close hook."""
        close = getattr(self._client, "close", None)
        if close is None:
            return
        result = close()
        if inspect.isawaitable(result):
            await result

    def model(
        self,
        model_name: str | None = None,
        *,
        settings: ModelSettings | None = None,
        profile: ModelProfileSpec | None = None,
        history_mode: HistoryMode | None = None,
    ) -> AcpModel:
        """Build an :class:`AcpModel` bound to this provider.

        When ``model_name`` is ``None``, the bridge does not call ACP
        ``session/set_config_option`` for ``"model"`` and leaves model selection
        to the wrapped agent's session default. The visible Pydantic AI model
        name remains ``"agent"``.
        """
        return AcpModel(
            model_name=model_name,
            provider=self,
            settings=settings,
            profile=profile,
            history_mode=history_mode,
        )

    async def render_prompt_blocks(
        self,
        messages: Sequence[ModelMessage],
        model_request_parameters: ModelRequestParameters,
        *,
        history_mode: HistoryMode | None = None,
    ) -> list[AgentPromptBlock]:
        """Render Pydantic AI model messages into ACP prompt blocks."""
        if self._prompt_renderer is None:
            return _default_render_prompt_blocks(
                messages,
                model_request_parameters,
                history_mode=history_mode or self._history_mode,
            )
        rendered = self._prompt_renderer(messages, model_request_parameters)
        if inspect.isawaitable(rendered):
            rendered = await rendered
        return list(cast(Sequence[AgentPromptBlock], rendered))

    async def request_prompt(
        self,
        *,
        model_name: str | None,
        prompt: Sequence[AgentPromptBlock],
        model_request_parameters: ModelRequestParameters,
    ) -> _AcpPromptResult:
        """Send one prompt turn to the ACP agent and collect its visible text.

        Errors raised by the ACP agent (rate limits, auth rejection, upstream
        API failures) are propagated with anyio TaskGroup wrapping stripped so
        the caller sees the agent's real error rather than an opaque
        ``ExceptionGroup: unhandled errors in a TaskGroup``.
        """
        try:
            session_id = await self._ensure_session(model_name=model_name)
            start_index = self._host.snapshot_index()
            request_meta = None
            if self._enable_pydantic_acp_meta:
                request_meta = build_structured_output_request_meta(model_request_parameters)
            prompt_kwargs: dict[str, Any] = {
                "prompt": list(prompt),
                "session_id": session_id,
                "message_id": uuid4().hex,
            }
            if request_meta is not None:
                prompt_kwargs["_meta"] = request_meta
            prompt_response = await self._client.prompt(**prompt_kwargs)
            text = await self._agent_message_text_after_prompt(
                start_index,
                session_id=session_id,
                prompt_response=prompt_response,
            )

            response_meta = extract_field_meta(prompt_response)
            usage = _usage_from_acp(getattr(prompt_response, "usage", None))
            if not usage.has_values():
                usage = self._host.usage_update_since(start_index, session_id=session_id)
            stop_reason = getattr(prompt_response, "stop_reason", None) or getattr(
                prompt_response,
                "stopReason",
                None,
            )
            return _AcpPromptResult(
                text=text,
                usage=usage,
                stop_reason=stop_reason,
                session_id=session_id,
                structured_output=extract_structured_output(response_meta),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            cleaned = _unwrap_acp_error(exc)
            if cleaned is exc:
                raise
            raise cleaned from None

    async def _agent_message_text_after_prompt(
        self,
        start_index: int,
        *,
        session_id: str,
        prompt_response: Any,
    ) -> str:
        text = self._host.agent_message_text_since(start_index, session_id=session_id)
        if text:
            return text

        text = _extract_text(prompt_response)
        if text:
            return text

        for _ in range(5):
            await asyncio.sleep(0)
            text = self._host.agent_message_text_since(start_index, session_id=session_id)
            if text:
                return text
        return ""

    async def _ensure_session(self, *, model_name: str | None) -> str:
        async with self._get_session_lock():
            if not self._initialized:
                init_response = await self._client.initialize(
                    protocol_version=self._protocol_version,
                    client_capabilities=self._client_capabilities or _default_client_capabilities(),
                    client_info=self._client_info,
                )
                self._auth_methods = list(getattr(init_response, "auth_methods", None) or [])
                self._initialized = True

            if self._session_id is None:
                session = await self._new_session_with_auth()
                self._session_id = session.session_id
                self._model_config_option_available = any(
                    isinstance(option, SessionConfigOptionSelect) and option.id == "model"
                    for option in session.config_options or []
                )

            if model_name is None:
                return self._session_id

            if self._current_model_name == model_name:
                return self._session_id

            if not self._model_config_option_available:
                raise UserError(
                    "The ACP agent does not expose a selectable 'model' session config option. "
                    "Use provider.model() to keep the agent default, or configure model selection "
                    "on the ACP agent."
                )
            await self._client.set_config_option(
                config_id="model",
                session_id=self._session_id,
                value=model_name,
            )
            self._current_model_name = model_name

        return self._session_id

    async def _new_session_with_auth(self) -> NewSessionResponse:
        """Create an ACP session, authenticating once if the agent demands it.

        ACP agents may reject ``session/new`` with an ``auth_required``
        (``-32000``) error until the client has authenticated. The base
        ``initialize``/``session/new`` handshake never calls ``authenticate``,
        so such agents are otherwise unrecoverable. This retries session
        creation exactly once after a successful ``authenticate`` call.
        """
        try:
            return await self._call_new_session()
        except RequestError as exc:
            if exc.code != RequestError.auth_required().code or self._authenticated:
                raise
            await self._authenticate()
            return await self._call_new_session()

    async def _call_new_session(self) -> NewSessionResponse:
        return await self._client.new_session(
            cwd=self._cwd,
            mcp_servers=list(self._mcp_servers),
        )

    async def _authenticate(self) -> None:
        """Run the ACP ``authenticate`` flow using an advertised auth method."""
        authenticate = getattr(self._client, "authenticate", None)
        if authenticate is None:
            raise UserError(
                f"{_AUTH_REQUIRED_DIAGNOSTIC} but the wrapped agent does not expose an "
                "'authenticate' method, so the session cannot be established."
            )
        agent_method = next(
            (method for method in self._auth_methods if isinstance(method, AuthMethodAgent)),
            None,
        )
        method_id = self._auth_method_id or (agent_method.id if agent_method is not None else None)
        if method_id is None:
            advertised = ", ".join(method.id for method in self._auth_methods)
            raise UserError(
                f"{_AUTH_REQUIRED_DIAGNOSTIC} but advertised no agent-managed authentication "
                f"method to satisfy it{f' (advertised: {advertised})' if advertised else ''}. "
                "Environment-variable and terminal methods require client-side setup. Complete "
                "that setup out of band, or pass auth_method_id=... after preparing the agent."
            )
        result = authenticate(method_id=method_id)
        if inspect.isawaitable(result):
            await result
        self._authenticated = True

    async def ensure_session(self, *, model_name: str | None = None) -> str:
        """Initialize the ACP connection and return an active session id.

        This is the public entry point for bootstrapping a session without
        sending a prompt turn: it performs ``initialize`` (authenticating if the
        agent demands it), creates the session, and optionally selects a model.
        Callers that need to configure a session up front (for example to set a
        session mode) should use this instead of reaching into the private
        ``_ensure_session``.
        """
        return await self._ensure_session(model_name=model_name)

    async def set_session_mode(self, mode_id: str) -> None:
        """Set the ACP session mode, bootstrapping the session if needed.

        ACP session modes (for example the permission mode) are distinct from
        the ``model`` session config option and are set via ``session/set_mode``.
        This ensures a session exists, then delegates to the wrapped agent's
        ``set_session_mode`` when available.
        """
        session_id = await self._ensure_session(model_name=None)
        set_mode = getattr(self._client, "set_session_mode", None)
        if set_mode is None:
            raise UserError(
                "The ACP agent does not expose 'set_session_mode', so the session "
                f"mode {mode_id!r} cannot be selected."
            )
        result = set_mode(session_id=session_id, mode_id=mode_id)
        if inspect.isawaitable(result):
            await result

    def _get_session_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._session_lock is None or self._session_lock_loop is not loop:
            self._session_lock = asyncio.Lock()
            self._session_lock_loop = loop
        return self._session_lock


# ---------------------------------------------------------------------------
# AcpModel
# ---------------------------------------------------------------------------


class AcpModel(Model[AcpAgent]):
    """Pydantic AI ``Model`` backed by an ACP agent provider."""

    _provider: Provider[AcpAgent]
    _history_mode: HistoryMode | None
    _model_name: str | None

    def __init__(
        self,
        model_name: str | None = None,
        *,
        provider: AcpProvider,
        settings: ModelSettings | None = None,
        profile: ModelProfileSpec | None = None,
        history_mode: HistoryMode | None = None,
    ) -> None:
        self._model_name = model_name
        self._history_mode = history_mode
        self._provider = provider
        super().__init__(
            settings=settings,
            profile=profile or _profile_for_provider(provider),
        )

    @property
    def model_name(self) -> str:
        return self._model_name or _DEFAULT_MODEL_NAME

    @property
    def system(self) -> str:
        return self._provider.name

    @property
    def base_url(self) -> str:
        return self._provider.base_url

    @classmethod
    def supported_native_tools(cls) -> frozenset[type[AbstractNativeTool]]:
        return frozenset()

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        raw_model_request_parameters = model_request_parameters
        model_settings, model_request_parameters = self.prepare_request(
            model_settings,
            model_request_parameters,
        )
        del model_settings
        self._ensure_supported_request(
            model_request_parameters,
            raw_params=raw_model_request_parameters,
        )
        provider = cast(AcpProvider, self._provider)
        prompt = await provider.render_prompt_blocks(
            messages,
            model_request_parameters,
            history_mode=self._history_mode,
        )
        acp_result = await provider.request_prompt(
            model_name=self._model_name,
            prompt=prompt,
            model_request_parameters=raw_model_request_parameters,
        )
        parts = self._response_parts(acp_result, raw_model_request_parameters)
        return ModelResponse(
            parts=parts,
            model_name=self.model_name,
            provider_name=self.system,
            provider_url=self.base_url,
            usage=acp_result.usage,
            finish_reason=_finish_reason_from_acp(acp_result.stop_reason),
            provider_details={"acp_session_id": acp_result.session_id},
        )

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any | None = None,
    ) -> AsyncGenerator[StreamedResponse, None]:
        response = await self.request(messages, model_settings, model_request_parameters)
        yield _AcpBufferedStreamedResponse(
            model_request_parameters=model_request_parameters,
            response=response,
        )

    def _ensure_supported_request(
        self,
        params: ModelRequestParameters,
        *,
        raw_params: ModelRequestParameters | None = None,
    ) -> None:
        raw_params = raw_params or params
        if params.function_tools:
            tool_names = ", ".join(tool.name for tool in params.function_tools)
            raise UserError(
                "AcpModel delegates a model turn to an ACP agent and cannot execute "
                f"Pydantic AI function tools directly: {tool_names}. Register tools on "
                "the ACP agent or provide them through an ACP host bridge instead.",
            )
        if params.native_tools:
            raise UserError(
                "AcpModel does not expose Pydantic AI native tools directly; use the "
                "ACP agent/provider side for native capabilities.",
            )
        if raw_params.output_tools or not params.allow_text_output:
            provider = cast(AcpProvider, self._provider)
            if provider.enable_pydantic_acp_meta and raw_params.output_tools:
                return
            raise UserError(
                "AcpModel is a text-response provider bridge. Structured/tool-only "
                "output must be implemented by the ACP agent or validated after the run.",
            )

    def _response_parts(
        self,
        acp_result: _AcpPromptResult,
        params: ModelRequestParameters,
    ) -> list[TextPart | ToolCallPart]:
        if acp_result.structured_output is not MISSING_STRUCTURED_OUTPUT:
            if not params.output_tools:
                raise UserError(
                    "ACP structured output metadata was returned, but the Pydantic AI "
                    "request did not include an output tool.",
                )
            output_tool = params.output_tools[0]
            return [
                ToolCallPart(
                    tool_name=output_tool.name,
                    args=acp_result.structured_output,
                    tool_kind=output_tool.tool_kind,
                    provider_name=self.system,
                ),
            ]
        if params.output_tools or not params.allow_text_output:
            raise UserError(
                "ACP agent did not return pydantic_acp structured output metadata for "
                "this structured-output request.",
            )
        if acp_result.text:
            return [TextPart(acp_result.text, provider_name=self.system)]
        if cast(AcpProvider, self._provider).raise_on_empty_turn:
            stop_reason = acp_result.stop_reason
            raise UnexpectedModelBehavior(
                "The ACP agent ended its turn without producing any text output"
                f"{f' (stop reason: {stop_reason})' if stop_reason else ''}. "
                "This usually means the ACP agent is unauthenticated, its ACP mode is "
                "not functioning, or it silently declined the request. Check that the "
                "underlying agent is logged in and that its ACP integration is working."
            )
        return []


# ---------------------------------------------------------------------------
# _AcpBufferedStreamedResponse
# ---------------------------------------------------------------------------


class _AcpBufferedStreamedResponse(StreamedResponse):
    __slots__ = (
        "_response",
        "_usage",
        "finish_reason",
        "provider_details",
        "provider_response_id",
    )

    def __init__(
        self,
        *,
        model_request_parameters: ModelRequestParameters,
        response: ModelResponse,
    ) -> None:
        super().__init__(model_request_parameters=model_request_parameters)
        self._response = response
        self._usage = response.usage
        self.provider_response_id = response.provider_response_id
        self.provider_details = response.provider_details
        self.finish_reason = response.finish_reason

    async def _get_event_iterator(
        self,
    ) -> AsyncGenerator[ModelResponseStreamEvent, None]:
        for part in self._response.parts:
            if not isinstance(part, TextPart):
                continue
            for event in self._parts_manager.handle_text_delta(
                vendor_part_id=part.id or "content",
                content=part.content,
            ):
                yield event

    async def close_stream(self) -> None:
        return None

    @property
    def model_name(self) -> str:
        return self._response.model_name or "acp"

    @property
    def provider_name(self) -> str | None:
        return self._response.provider_name

    @property
    def provider_url(self) -> str | None:
        return self._response.provider_url

    @property
    def timestamp(self) -> datetime:
        return self._response.timestamp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_render_prompt_blocks(
    messages: Sequence[ModelMessage],
    model_request_parameters: ModelRequestParameters,
    *,
    history_mode: HistoryMode = "latest_user",
) -> list[AgentPromptBlock]:
    del model_request_parameters
    if history_mode == "latest_user":
        latest = _latest_model_request(messages)
        if latest is not None:
            text = _render_message(latest, include_role=False)
            if text.strip():
                return [text_block(text)]
    if not messages:
        return []
    full_text = "\n\n".join(
        _render_message(message, include_role=True) for message in messages
    ).strip()
    return [text_block(full_text)] if full_text else []


def _latest_model_request(messages: Sequence[ModelMessage]) -> ModelMessage | None:
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            return message
    return messages[-1] if messages else None


def _render_message(message: ModelMessage, *, include_role: bool) -> str:
    if isinstance(message, ModelRequest):
        text = _render_request_as_text(message)
    else:
        text = message.text or ""
    if not include_role or not text:
        return text
    role = "user" if isinstance(message, ModelRequest) else "assistant"
    return f"<{role}>\n{text}\n{role}>"


def _render_request_as_text(request: ModelRequest) -> str:
    rendered: list[str] = []
    if request.instructions:
        rendered.append(_section("Instructions", request.instructions))
    for part in request.parts:
        if isinstance(part, UserPromptPart):
            rendered.append(_render_user_prompt(part))
        elif isinstance(part, SystemPromptPart):
            rendered.append(_section("System", part.content))
        elif isinstance(part, ToolReturnPart):
            rendered.append(_render_tool_return(part))
        elif isinstance(part, RetryPromptPart):
            rendered.append(part.model_response())
    return "\n\n".join(item for item in rendered if item).strip()


def _render_user_prompt(part: UserPromptPart) -> str:
    return _render_user_content(part.content)


def _render_user_content(content: str | Sequence[UserContent]) -> str:
    if isinstance(content, str):
        return content
    rendered: list[str] = []
    for item in content:
        rendered.append(_render_user_content_item(item))
    return "\n\n".join(item for item in rendered if item)


def _render_user_content_item(item: UserContent) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, TextContent):
        return item.content
    if isinstance(item, BinaryContent):
        return f"[binary:{item.media_type}:{len(item.data)} bytes]"
    if isinstance(item, ImageUrl | AudioUrl | DocumentUrl | VideoUrl):
        return f"[{item.kind}:{item.url}]"
    if isinstance(item, UploadedFile):
        return f"[uploaded-file:{item.provider_name}:{item.file_id}]"
    kind = getattr(item, "kind", type(item).__name__)
    return f"[{kind}]"


def _render_tool_return(part: ToolReturnPart) -> str:
    label = part.tool_name or part.tool_call_id
    content = part.model_response_str()
    return _section(f"Tool result: {label}", content)


def _section(title: str, content: str) -> str:
    content = content.strip()
    if not content:
        return ""
    return f"{title}:\n{content}"


def _is_agent_message_chunk(update: Any) -> bool:
    return (
        isinstance(update, AgentMessageChunk)
        or getattr(update, "session_update", None) == "agent_message_chunk"
    )


def _usage_from_acp(value: Any) -> RequestUsage:
    if value is None:
        return RequestUsage()
    details: dict[str, int] = {}
    thought_tokens = _int_attr(value, "thought_tokens")
    if thought_tokens:
        details["reasoning_tokens"] = thought_tokens
    return RequestUsage(
        input_tokens=_int_attr(value, "input_tokens"),
        cache_write_tokens=_int_attr(value, "cached_write_tokens"),
        cache_read_tokens=_int_attr(value, "cached_read_tokens"),
        output_tokens=_int_attr(value, "output_tokens"),
        details=details,
    )


def _int_attr(value: Any, name: str) -> int:
    raw_value = getattr(value, name, 0) or 0
    return int(raw_value)


def _finish_reason_from_acp(stop_reason: str | None) -> FinishReason:
    if stop_reason in (None, "end_turn", "stop"):
        return "stop"
    if stop_reason in ("max_tokens", "length"):
        return "length"
    if stop_reason == "cancelled":
        return "error"
    return "stop"


def _profile_for_provider(provider: AcpProvider) -> ModelProfile:
    if provider.enable_pydantic_acp_meta:
        return _ACP_META_MODEL_PROFILE
    return ACP_MODEL_PROFILE


def _agent_supports_pydantic_acp_meta(acp_agent: AcpAgent) -> bool:
    return getattr(acp_agent, "_pydantic_acp_meta_supported", False) is True


_TEXT_FIELD_NAMES: tuple[str, ...] = ("text", "delta", "message", "output_text", "response", "data")


def _extract_text(value: Any) -> str:
    """Recursively collect text from an ACP response or session-update payload."""
    return "".join(
        candidate for candidate in _walk_text_candidates(value) if isinstance(candidate, str)
    )


def _walk_text_candidates(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, AgentMessageChunk):
        content = getattr(value, "content", None)
        text = getattr(content, "text", None)
        return [text] if isinstance(text, str) else []
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        items: list[Any] = []
        for item in value:
            items.extend(_walk_text_candidates(item))
        return items
    candidates: list[Any] = []
    content = _response_field(value, "content")
    if content is not None and content is not value:
        candidates.extend(_walk_text_candidates(content))
    for name in _TEXT_FIELD_NAMES:
        attr = _response_field(value, name)
        if isinstance(attr, str):
            candidates.append(attr)
    return candidates


def _response_field(response: Any, *names: str) -> Any:
    for name in names:
        if isinstance(response, dict) and name in response:
            return response[name]
        if hasattr(response, name):
            return getattr(response, name)
    return None
