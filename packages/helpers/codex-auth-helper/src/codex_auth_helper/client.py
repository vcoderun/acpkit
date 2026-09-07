from __future__ import annotations as _annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractContextManager, asynccontextmanager, suppress
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import httpx
from openai import AsyncOpenAI, AuthenticationError, NotGiven, Omit, OpenAI
from openai._models import FinalRequestOptions
from typing_extensions import override

from ._headers import CODEX_USER_AGENT, codex_request_headers
from ._responses_lite import (
    RESPONSES_LITE_HEADER,
    CodexResponsesTransport,
    prepare_responses_lite_options,
)
from ._responses_turn import ResponsesTurnContext, ResponsesTurnState
from ._responses_websocket import (
    CodexResponsesConnection,
    CodexResponsesConnectionError,
    CodexResponsesFallback,
    CodexResponsesProtocolError,
    CodexResponsesSessionInfo,
    CodexResponsesTransportEvent,
    CodexResponsesTransportObserver,
    CodexResponsesTransportPhase,
    CodexWebSocketResponseStream,
    validate_responses_connection,
    websocket_response_create_event,
)
from .auth import CodexAuthConfig, CodexAuthStore, CodexTokenManager
from .auth.errors import CodexAuthAccountMismatchError, CodexAuthRefreshError

if TYPE_CHECKING:
    from openai.types.websocket_connection_options import WebSocketConnectionOptions

__all__ = (
    "CodexAsyncOpenAI",
    "CodexOpenAI",
    "CodexResponsesConnection",
    "CodexResponsesFallback",
    "CodexResponsesTransport",
    "create_codex_async_openai",
    "create_codex_openai",
)


@dataclass
class _ResponsesSessionState:
    info: CodexResponsesSessionInfo
    fallback: CodexResponsesFallback
    manager: Any = None
    connection: Any = None
    connection_options: tuple[dict[str, str], dict[str, Any]] | None = None
    last_response_id: str | None = None
    history_snapshot: tuple[Any, ...] | None = None
    request_contract: str | None = None
    in_flight: bool = False
    session_id: str = ""
    attempt: int = 0
    continuation_reason: str = "full_request"
    acknowledged_message_count: int = 0
    replay_safe: bool = False

    def reset_chain(self) -> None:
        self.last_response_id = None
        self.history_snapshot = None
        self.request_contract = None

    async def close_connection(self) -> None:
        self.reset_chain()
        manager = self.manager
        connection = self.connection
        self.manager = None
        self.connection = None
        self.connection_options = None
        try:
            if manager is not None:
                await manager.__aexit__(None, None, None)
            elif connection is not None:
                await connection.close()
        finally:
            self.in_flight = False


class _CodexAsyncResponsesProxy:
    def __init__(self, client: CodexAsyncOpenAI) -> None:
        self._client = client

    async def create(self, **kwargs: Any) -> Any:
        return await self._client._create_response(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client._http_responses, name)


def _resolve_default_headers(
    token_manager: CodexTokenManager,
    headers: dict[str, Any],
    transport: CodexResponsesTransport,
) -> None:
    account_id = token_manager.current_account_id
    if account_id is not None:
        headers["ChatGPT-Account-Id"] = account_id
    headers.setdefault("originator", "codex-auth-helper")
    if transport == "responses_lite":
        headers[RESPONSES_LITE_HEADER] = "true"


def _validate_transport(transport: CodexResponsesTransport) -> None:
    if transport not in ("responses", "responses_lite"):
        raise ValueError(f"Unsupported Codex Responses transport: {transport!r}.")


class CodexAsyncOpenAI(AsyncOpenAI):
    def __init__(
        self,
        *,
        base_url: str,
        http_client: Any | None,
        token_manager: CodexTokenManager,
        owns_http_client: bool,
        transport: CodexResponsesTransport = "responses",
        transport_observer: CodexResponsesTransportObserver | None = None,
    ) -> None:
        _validate_transport(transport)
        self.token_manager = token_manager
        self._owns_http_client = owns_http_client
        self.responses_transport: CodexResponsesTransport = transport
        self.responses_transport_observer = transport_observer
        self._turn_context = ResponsesTurnContext()
        super().__init__(
            api_key=token_manager.get_access_token,
            base_url=base_url,
            http_client=http_client,
        )
        self._http_responses = self.responses
        self._responses_session_var: ContextVar[_ResponsesSessionState | None] = ContextVar(
            f"codex_responses_session_{id(self)}",
            default=None,
        )
        self.__dict__["responses"] = _CodexAsyncResponsesProxy(self)

    @asynccontextmanager
    async def responses_session(
        self,
        *,
        connection: CodexResponsesConnection = "http",
        fallback: CodexResponsesFallback = "error",
        isolate: bool = False,
    ) -> AsyncIterator[CodexResponsesSessionInfo]:
        validate_responses_connection(connection, fallback)
        if connection == "websocket" and self.responses_transport == "responses_lite":
            raise ValueError("`responses_lite` does not support the WebSocket connection.")
        current = self.current_responses_session()
        if current is not None and not isolate:
            if current.info.requested_connection != connection or current.fallback != fallback:
                raise CodexResponsesProtocolError(
                    "Nested Responses sessions must use the outer connection policy."
                )
            yield current.info
            return

        info = CodexResponsesSessionInfo(
            requested_connection=connection,
            effective_connection="http" if connection == "http" else None,
        )
        state = _ResponsesSessionState(
            info=info,
            fallback=fallback,
            session_id=uuid4().hex,
        )
        token = self._responses_session_var.set(state)
        body_failed = False
        try:
            yield info
        except BaseException:
            body_failed = True
            raise
        finally:
            try:
                await state.close_connection()
            except Exception:
                if not body_failed:
                    raise
            finally:
                self._responses_session_var.reset(token)

    def current_responses_session(self) -> _ResponsesSessionState | None:
        return self._responses_session_var.get()

    async def recover_response(self, exc: Exception, *, retries: int) -> bool:
        """Recover an uncommitted, non-streaming framework model response only.

        The caller must rebuild the complete native request, not replay a delta
        or rerun its agent/tools. Never call this after exposing partial output.
        """
        state = self.current_responses_session()
        if (
            state is None
            or state.info.requested_connection != "websocket"
            or state.info.effective_connection == "http"
            or not state.replay_safe
            or not _transient_response_failure(exc)
        ):
            return False
        fallback = retries == self.max_retries and state.fallback == "http"
        if retries >= self.max_retries and not fallback:
            return False
        with suppress(Exception):
            await state.close_connection()
        state.continuation_reason = "transport_recovery"
        state.acknowledged_message_count = 0
        if fallback:
            state.info.effective_connection = "http"
            state.info.fallback_used = True
        self._observe(
            state,
            {},
            phase="fallback" if fallback else "retry",
            failure_category="transport",
        )
        await asyncio.sleep(min(0.25 * 2**retries, 2.0))
        return True

    @asynccontextmanager
    async def responses_turn(self) -> AsyncIterator[None]:
        """Scope server routing state to one user turn, including its tool calls."""
        with self._turn_context.scope():
            yield

    def _responses_connection_reset_reason(self, request: Mapping[str, Any]) -> str | None:
        state = self.current_responses_session()
        if state is None or state.connection is None:
            return None
        # The SDK exposes socket lifecycle through its underlying websockets connection.
        socket = getattr(state.connection, "_connection", None)
        if getattr(socket, "close_code", None) is not None:
            return "connection_closed"
        options = _responses_connection_options(request)
        if state.connection_options != options:
            return "connection_options_changed"
        return None

    async def _create_response(self, **kwargs: Any) -> Any:
        state = self.current_responses_session()
        if state is None:
            return await self._http_responses.create(**kwargs)
        if state.info.requested_connection == "http" or state.info.effective_connection == "http":
            state.attempt += 1
            try:
                response = await self._http_responses.create(**kwargs)
            except Exception:
                self._observe(
                    state,
                    kwargs,
                    phase="failed",
                    failure_category="http",
                )
                raise
            self._observe(
                state,
                kwargs,
                phase="submitted",
            )
            return response
        if state.in_flight:
            raise CodexResponsesProtocolError(
                "A Responses WebSocket session supports one in-flight response at a time."
            )

        # Remote/built-in tools may already have executed in a lost response.
        event = websocket_response_create_event(kwargs)
        tools = event.get("tools")
        state.replay_safe = (
            isinstance(tools, (NotGiven, Omit))
            or tools is None
            or isinstance(tools, list)
            and all(isinstance(tool, Mapping) and tool.get("type") == "function" for tool in tools)
        ) and not event.get("background")

        turn = self._turn_context.current()
        previous_response_id = event.get("previous_response_id")
        reset_reason = self._responses_connection_reset_reason(kwargs)
        if reset_reason is not None and previous_response_id is not None:
            raise CodexResponsesProtocolError(
                "The Responses WebSocket connection must be renewed. "
                "Send full input without previous_response_id; no request was sent."
            )
        if previous_response_id is not None and previous_response_id != state.last_response_id:
            raise CodexResponsesProtocolError(
                "The requested previous response is not owned by the active WebSocket session."
            )
        state.attempt += 1
        state.in_flight = True
        try:
            if reset_reason is not None:
                await state.close_connection()
                state.in_flight = True
                state.continuation_reason = reset_reason
                state.acknowledged_message_count = 0
            connection = await self._ensure_responses_connection(state, kwargs)
        except CodexResponsesConnectionError:
            state.in_flight = False
            self._observe(
                state,
                event,
                phase="failed",
                failure_category="handshake",
            )
            raise
        except BaseException:
            state.in_flight = False
            raise
        if connection is None:
            state.in_flight = False
            self._observe(
                state,
                event,
                phase="fallback",
            )
            return await self._http_responses.create(**kwargs)
        if turn is not None:
            metadata = turn.apply(_mapping_or_empty(event.get("client_metadata")))
            if metadata:
                event["client_metadata"] = metadata
            else:
                event.pop("client_metadata", None)
        try:
            await connection.send(event)
        except asyncio.CancelledError:
            with suppress(Exception):
                await state.close_connection()
            raise
        except Exception as exc:
            with suppress(Exception):
                await state.close_connection()
            self._observe(
                state,
                event,
                phase="failed",
                failure_category="send",
            )
            raise CodexResponsesProtocolError(
                "The Responses WebSocket request could not be confirmed as sent; "
                "it was not replayed."
            ) from exc
        self._observe(
            state,
            event,
            phase="submitted",
        )

        def completed(response_id: str) -> None:
            state.last_response_id = response_id
            state.in_flight = False
            self._observe(
                state,
                event,
                phase="completed",
            )

        async def abandoned() -> None:
            try:
                await state.close_connection()
            finally:
                self._observe(
                    state,
                    event,
                    phase="abandoned",
                )

        return CodexWebSocketResponseStream(
            connection,
            on_completed=completed,
            on_abandoned=abandoned,
            on_failed=lambda category: self._observe(
                state,
                event,
                phase="failed",
                failure_category=category,
            ),
            turn=turn,
        )

    def _observe(
        self,
        state: _ResponsesSessionState,
        request: Mapping[str, Any],
        *,
        phase: CodexResponsesTransportPhase,
        failure_category: str | None = None,
    ) -> None:
        observer = self.responses_transport_observer
        if observer is None:
            return
        try:
            observer(
                _transport_event(state, request, phase=phase, failure_category=failure_category)
            )
        except Exception:
            state.info.observer_failures += 1

    async def _ensure_responses_connection(
        self,
        state: _ResponsesSessionState,
        request: Mapping[str, Any],
    ) -> Any | None:
        if state.connection is not None:
            return state.connection

        extra_headers, extra_query = _responses_connection_options(request)
        connection_options = deepcopy((extra_headers, extra_query))
        turn = self._turn_context.current()
        if turn is not None:
            extra_headers = turn.apply(extra_headers)
        access_token = await self.token_manager.get_access_token()
        extra_headers["Authorization"] = f"Bearer {access_token}"
        _resolve_default_headers(
            self.token_manager,
            extra_headers,
            self.responses_transport,
        )
        try:
            recovery = iter((False, True))
            while True:
                try:
                    manager = self._http_responses.connect(
                        extra_headers=extra_headers,
                        extra_query=extra_query,
                        # The SDK forwards this option, but its generated type omits it.
                        websocket_connection_options=cast(
                            "WebSocketConnectionOptions", {"close_timeout": 0.25}
                        ),
                    )
                    connection = await manager.__aenter__()
                    if turn is not None:
                        socket = getattr(connection, "_connection", None)
                        handshake = getattr(socket, "response", None)
                        turn.capture(getattr(handshake, "headers", None))
                    break
                except Exception as exc:
                    if _handshake_status(exc) != 401:
                        raise
                    token = None
                    while token is None:
                        refresh = next(recovery, None)
                        if refresh is None:
                            raise
                        token = await self.token_manager.recover_after_unauthorized(
                            access_token, refresh=refresh
                        )
                    access_token = token
                    _set_auth_headers(extra_headers, token, self.token_manager.current_account_id)
        except Exception as exc:
            if (
                state.fallback == "http"
                and state.info.effective_connection is None
                and _handshake_status(exc) != 401
                and not isinstance(exc, (CodexAuthAccountMismatchError, CodexAuthRefreshError))
            ):
                state.info.effective_connection = "http"
                state.info.fallback_used = True
                return None
            raise CodexResponsesConnectionError(
                "Could not establish the requested Codex Responses WebSocket connection."
            ) from exc

        state.manager = manager
        state.connection = connection
        state.connection_options = connection_options
        state.info.effective_connection = "websocket"
        return connection

    @property
    @override
    def default_headers(self) -> dict[str, str | Omit]:
        headers = dict(super().default_headers)
        headers["User-Agent"] = CODEX_USER_AGENT
        _resolve_default_headers(self.token_manager, headers, self.responses_transport)
        return headers

    @override
    async def _prepare_options(self, options: FinalRequestOptions) -> FinalRequestOptions:
        prepared = await super()._prepare_options(options)
        _prepare_responses_headers(prepared, self._turn_context.current())
        if self.responses_transport == "responses_lite":
            return prepare_responses_lite_options(prepared)
        return prepared

    @override
    async def request(
        self,
        cast_to: type[Any],
        options: FinalRequestOptions,
        *,
        stream: bool = False,
        stream_cls: Any = None,
    ) -> Any:
        recovery = iter((False, True))
        while True:
            try:
                return await super().request(cast_to, options, stream=stream, stream_cls=stream_cls)
            except AuthenticationError as exc:
                request = exc.response.request
                rejected = _bearer_token(request)
                if not _can_recover_auth(request, options, self.base_url) or rejected is None:
                    raise
                token = None
                while token is None:
                    refresh = next(recovery, None)
                    if refresh is None:
                        raise
                    token = await self.token_manager.recover_after_unauthorized(
                        rejected, refresh=refresh
                    )

    @override
    async def _process_response(self, *, response: Any, **kwargs: Any) -> Any:
        if response.is_success and _is_responses_request(response.request, self.base_url):
            turn = self._turn_context.current()
            if turn is not None:
                turn.capture(response.headers)
        return await super()._process_response(response=response, **kwargs)

    @override
    async def close(self) -> None:
        if self._owns_http_client:
            await super().close()
        await self.token_manager.close()


class CodexOpenAI(OpenAI):
    def __init__(
        self,
        *,
        base_url: str,
        http_client: Any | None,
        token_manager: CodexTokenManager,
        owns_http_client: bool,
        transport: CodexResponsesTransport = "responses",
    ) -> None:
        _validate_transport(transport)
        self.token_manager = token_manager
        self._owns_http_client = owns_http_client
        self.responses_transport: CodexResponsesTransport = transport
        self._turn_context = ResponsesTurnContext()
        super().__init__(
            api_key=token_manager.get_access_token_sync,
            base_url=base_url,
            http_client=http_client,
        )

    @property
    @override
    def default_headers(self) -> dict[str, str | Omit]:
        headers = dict(super().default_headers)
        headers["User-Agent"] = CODEX_USER_AGENT
        _resolve_default_headers(self.token_manager, headers, self.responses_transport)
        return headers

    @override
    def _prepare_options(self, options: FinalRequestOptions) -> FinalRequestOptions:
        prepared = super()._prepare_options(options)
        _prepare_responses_headers(prepared, self._turn_context.current())
        if self.responses_transport == "responses_lite":
            return prepare_responses_lite_options(prepared)
        return prepared

    def responses_turn(self) -> AbstractContextManager[None]:
        """Scope server routing state to one synchronous user turn."""
        return self._turn_context.scope()

    @override
    def request(
        self,
        cast_to: type[Any],
        options: FinalRequestOptions,
        *,
        stream: bool = False,
        stream_cls: Any = None,
    ) -> Any:
        recovery = iter((False, True))
        while True:
            try:
                return super().request(cast_to, options, stream=stream, stream_cls=stream_cls)
            except AuthenticationError as exc:
                request = exc.response.request
                rejected = _bearer_token(request)
                if not _can_recover_auth(request, options, self.base_url) or rejected is None:
                    raise
                token = None
                while token is None:
                    refresh = next(recovery, None)
                    if refresh is None:
                        raise
                    token = self.token_manager.recover_after_unauthorized_sync(
                        rejected, refresh=refresh
                    )

    @override
    def _process_response(self, *, response: Any, **kwargs: Any) -> Any:
        if response.is_success and _is_responses_request(response.request, self.base_url):
            turn = self._turn_context.current()
            if turn is not None:
                turn.capture(response.headers)
        return super()._process_response(response=response, **kwargs)

    @override
    def close(self) -> None:
        if self._owns_http_client:
            super().close()
        if self.token_manager.owns_http_client:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(self.token_manager.close())
            else:
                loop.create_task(self.token_manager.close())


def create_codex_async_openai(
    *,
    config: CodexAuthConfig | None = None,
    http_client: Any | None = None,
    auth_http_client: httpx.AsyncClient | None = None,
    transport: CodexResponsesTransport = "responses",
    transport_observer: CodexResponsesTransportObserver | None = None,
) -> CodexAsyncOpenAI:
    resolved_config = config or CodexAuthConfig()
    owns_http_client = http_client is None
    owns_auth_http_client = auth_http_client is None
    resolved_auth_http_client = auth_http_client or httpx.AsyncClient(
        follow_redirects=True,
        timeout=resolved_config.timeout_seconds,
    )
    token_manager = CodexTokenManager(
        config=resolved_config,
        store=CodexAuthStore(resolved_config.auth_path),
        http_client=resolved_auth_http_client,
        owns_http_client=owns_auth_http_client,
    )
    return CodexAsyncOpenAI(
        base_url=resolved_config.api_base_url,
        http_client=http_client,
        token_manager=token_manager,
        owns_http_client=owns_http_client,
        transport=transport,
        transport_observer=transport_observer,
    )


def create_codex_openai(
    *,
    config: CodexAuthConfig | None = None,
    http_client: Any | None = None,
    auth_http_client: httpx.AsyncClient | None = None,
    transport: CodexResponsesTransport = "responses",
) -> CodexOpenAI:
    resolved_config = config or CodexAuthConfig()
    owns_http_client = http_client is None
    owns_auth_http_client = auth_http_client is None
    resolved_auth_http_client = auth_http_client or httpx.AsyncClient(
        follow_redirects=True,
        timeout=resolved_config.timeout_seconds,
    )
    token_manager = CodexTokenManager(
        config=resolved_config,
        store=CodexAuthStore(resolved_config.auth_path),
        http_client=resolved_auth_http_client,
        owns_http_client=owns_auth_http_client,
    )
    return CodexOpenAI(
        base_url=resolved_config.api_base_url,
        http_client=http_client,
        token_manager=token_manager,
        owns_http_client=owns_http_client,
        transport=transport,
    )


def _prepare_responses_headers(
    options: FinalRequestOptions, turn: ResponsesTurnState | None = None
) -> None:
    if options.url.rstrip("/") != "/responses" or not isinstance(options.json_data, Mapping):
        return
    body = {**options.json_data, **_mapping_or_empty(options.extra_json)}
    options.headers = codex_request_headers(
        _string_headers(options.headers),
        model=body.get("model"),
        service_tier=body.get("service_tier"),
    )
    if turn is not None:
        options.headers = turn.apply(options.headers)


def _is_responses_request(request: Any, base_url: Any) -> bool:
    return (
        request.url.scheme == base_url.scheme
        and request.url.host == base_url.host
        and request.url.port == base_url.port
        and request.url.path.rstrip("/") == base_url.path.rstrip("/") + "/responses"
    )


def _can_recover_auth(request: Any, options: FinalRequestOptions, base_url: Any) -> bool:
    headers = _mapping_or_empty(options.headers)
    return _is_responses_request(request, base_url) and not any(
        key.lower() == "authorization" for key in headers
    )


def _bearer_token(request: Any) -> str | None:
    value = request.headers.get("authorization", "")
    return value[7:] if value.startswith("Bearer ") and len(value) > 7 else None


def _handshake_status(error: Exception) -> int | None:
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None) or getattr(error, "status_code", None)
    return status if isinstance(status, int) else None


def _set_auth_headers(headers: Any, token: str, account_id: str | None) -> None:
    headers["Authorization"] = f"Bearer {token}"
    if account_id is None:
        headers.pop("ChatGPT-Account-Id", None)
    else:
        headers["ChatGPT-Account-Id"] = account_id


def _responses_connection_options(
    request: Mapping[str, Any],
) -> tuple[dict[str, str], dict[str, Any]]:
    body = {**request, **_mapping_or_empty(request.get("extra_body"))}
    return (
        codex_request_headers(
            _string_headers(request.get("extra_headers")),
            model=body.get("model"),
            service_tier=body.get("service_tier"),
            websocket=True,
        ),
        _mapping_or_empty(request.get("extra_query")),
    )


def _string_headers(value: Any) -> dict[str, str]:
    if value is None or isinstance(value, (NotGiven, Omit)):
        return {}
    if not isinstance(value, Mapping):
        raise CodexResponsesProtocolError("Responses `extra_headers` must be a mapping.")
    return {
        str(key): str(item) for key, item in value.items() if not isinstance(item, (NotGiven, Omit))
    }


def _transient_response_failure(exc: Exception) -> bool:
    from websockets.exceptions import ConnectionClosed

    if isinstance(
        exc,
        (
            CodexAuthAccountMismatchError,
            CodexAuthRefreshError,
            AuthenticationError,
            PermissionError,
        ),
    ):
        return False
    if isinstance(exc, (CodexResponsesConnectionError, CodexResponsesProtocolError)):
        return isinstance(exc.__cause__, Exception) and _transient_response_failure(exc.__cause__)
    status = _handshake_status(exc)
    if status is not None:
        return status in {408, 429} or status >= 500
    return isinstance(exc, (ConnectionClosed, OSError, TimeoutError, httpx.TransportError))


def _mapping_or_empty(value: Any) -> dict[str, Any]:
    if value is None or isinstance(value, (NotGiven, Omit)):
        return {}
    if not isinstance(value, Mapping):
        raise CodexResponsesProtocolError("Responses `extra_query` must be a mapping.")
    return dict(value)


def _transport_event(
    state: _ResponsesSessionState,
    request: Mapping[str, Any],
    *,
    phase: CodexResponsesTransportPhase,
    failure_category: str | None = None,
) -> CodexResponsesTransportEvent:
    measured_request = {
        key: value for key, value in request.items() if not isinstance(value, (NotGiven, Omit))
    }
    input_value = measured_request.get("input")
    if input_value is None:
        input_item_count = 0
    elif isinstance(input_value, (list, tuple)):
        input_item_count = len(input_value)
    else:
        input_item_count = 1
    return CodexResponsesTransportEvent(
        session_id=state.session_id,
        attempt=state.attempt,
        phase=phase,
        requested_connection=state.info.requested_connection,
        effective_connection=state.info.effective_connection,
        fallback_used=state.info.fallback_used,
        continuation=isinstance(
            measured_request.get("previous_response_id"),
            str,
        ),
        continuation_reason=state.continuation_reason,
        acknowledged_message_count=state.acknowledged_message_count,
        input_item_count=input_item_count,
        input_bytes=_json_bytes(input_value),
        request_bytes=_json_bytes(measured_request),
        failure_category=failure_category,
    )


def _json_bytes(value: Any) -> int | None:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=_json_default,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return len(encoded)


def _json_default(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    raise TypeError
