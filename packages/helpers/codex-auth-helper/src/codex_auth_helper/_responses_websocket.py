from __future__ import annotations as _annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from openai import NotGiven, Omit

from ._responses_turn import ResponsesTurnState

CodexResponsesConnection = Literal["http", "websocket"]
CodexResponsesFallback = Literal["error", "http"]
CodexResponsesTransportPhase = Literal[
    "submitted",
    "completed",
    "abandoned",
    "fallback",
    "failed",
]

_HTTP_ONLY_FIELDS = frozenset({"extra_body", "extra_headers", "extra_query", "stream", "timeout"})
_TERMINAL_EVENTS = frozenset({"response.completed", "response.failed", "response.incomplete"})


class CodexResponsesConnectionError(RuntimeError):
    """Raised when an explicitly requested Responses WebSocket cannot be opened."""


class CodexResponsesProtocolError(RuntimeError):
    """Raised when a WebSocket response cannot be handled without unsafe replay."""


@dataclass
class CodexResponsesSessionInfo:
    """Sanitized connection outcome for one logical model run."""

    requested_connection: CodexResponsesConnection
    effective_connection: CodexResponsesConnection | None = None
    fallback_used: bool = False
    observer_failures: int = 0


@dataclass(frozen=True)
class CodexResponsesTransportEvent:
    """Content-free evidence for one Responses transport attempt."""

    session_id: str
    attempt: int
    phase: CodexResponsesTransportPhase
    requested_connection: CodexResponsesConnection
    effective_connection: CodexResponsesConnection | None
    fallback_used: bool
    continuation: bool
    continuation_reason: str
    acknowledged_message_count: int
    input_item_count: int
    input_bytes: int | None
    request_bytes: int | None
    failure_category: str | None = None


CodexResponsesTransportObserver: TypeAlias = Callable[
    [CodexResponsesTransportEvent],
    None,
]


def validate_responses_connection(
    connection: CodexResponsesConnection,
    fallback: CodexResponsesFallback,
) -> None:
    if connection not in ("http", "websocket"):
        raise ValueError(f"Unsupported Codex Responses connection: {connection!r}.")
    if fallback not in ("error", "http"):
        raise ValueError(f"Unsupported Codex Responses fallback: {fallback!r}.")
    if connection == "http" and fallback != "error":
        raise ValueError("`fallback` applies only when `connection='websocket'`.")


def websocket_response_create_event(request: Mapping[str, Any]) -> dict[str, Any]:
    """Convert final Responses HTTP kwargs to the native WebSocket event shape."""

    stream = request.get("stream")
    if not _is_omitted(stream) and stream is not True:
        raise CodexResponsesProtocolError("Responses WebSocket requests must use streaming output.")
    background = request.get("background")
    if not _is_omitted(background) and background not in (None, False):
        raise CodexResponsesProtocolError(
            "Background Responses are not supported over the WebSocket connection."
        )
    timeout = request.get("timeout")
    if not _is_omitted(timeout) and timeout is not None:
        raise CodexResponsesProtocolError(
            "Per-request timeouts are not supported over the Responses WebSocket "
            "connection. Configure the client connection timeout instead."
        )

    event = {
        key: value
        for key, value in request.items()
        if key not in _HTTP_ONLY_FIELDS and not _is_omitted(value)
    }
    event.pop("background", None)

    extra_body = request.get("extra_body")
    if not _is_omitted(extra_body) and extra_body is not None:
        if not isinstance(extra_body, Mapping):
            raise CodexResponsesProtocolError("Responses WebSocket `extra_body` must be a mapping.")
        overlap = event.keys() & extra_body.keys()
        if overlap:
            names = ", ".join(sorted(overlap))
            raise CodexResponsesProtocolError(
                f"Responses WebSocket `extra_body` conflicts with request fields: {names}."
            )
        event.update(extra_body)

    event["type"] = "response.create"
    return event


class CodexWebSocketResponseStream:
    """Minimal AsyncStream-compatible view over one WebSocket response."""

    def __init__(
        self,
        connection: Any,
        *,
        on_completed: Callable[[str], None],
        on_abandoned: Callable[[], Awaitable[None]],
        turn: ResponsesTurnState | None = None,
    ) -> None:
        self._connection = connection
        self._on_completed = on_completed
        self._on_abandoned = on_abandoned
        self._consumed = False
        self._terminal = False
        self._turn = turn

    async def __aenter__(self) -> CodexWebSocketResponseStream:
        return self

    async def __aexit__(self, exc_type: object, *_exc_info: object) -> None:
        if not self._terminal:
            if exc_type is None:
                await self._abandon()
            else:
                await self._abandon_safely()

    def __aiter__(self) -> AsyncIterator[Any]:
        if self._consumed:
            raise CodexResponsesProtocolError(
                "A Responses WebSocket stream can only be consumed once."
            )
        self._consumed = True
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[Any]:
        try:
            async for event in self._connection:
                event_type = getattr(event, "type", None)
                if event_type == "codex.response.metadata":
                    if self._turn is not None:
                        self._turn.capture(getattr(event, "headers", None))
                    # Routing metadata is transport state, not a model stream event.
                    continue
                if event_type == "error":
                    raise CodexResponsesProtocolError(
                        "The Responses WebSocket returned an error event."
                    )
                if event_type in _TERMINAL_EVENTS:
                    if event_type == "response.completed":
                        response_id = _response_id(event)
                        if response_id is None:
                            raise CodexResponsesProtocolError(
                                "A completed Responses WebSocket event had no response id."
                            )
                        self._terminal = True
                        self._on_completed(response_id)
                    else:
                        await self._abandon_safely()
                    yield event
                    return
                yield event
            await self._abandon_safely()
            raise CodexResponsesProtocolError(
                "The Responses WebSocket closed before a terminal response event."
            )
        except BaseException:
            await self._abandon_safely()
            raise

    async def _abandon(self) -> None:
        if self._terminal:
            return
        self._terminal = True
        await self._on_abandoned()

    async def _abandon_safely(self) -> None:
        with suppress(Exception):
            await self._abandon()


def _response_id(event: Any) -> str | None:
    response = getattr(event, "response", None)
    response_id = getattr(response, "id", None) or getattr(event, "response_id", None)
    return response_id if isinstance(response_id, str) and response_id else None


def _is_omitted(value: Any) -> bool:
    return isinstance(value, (NotGiven, Omit))


__all__ = (
    "CodexResponsesConnection",
    "CodexResponsesConnectionError",
    "CodexResponsesFallback",
    "CodexResponsesProtocolError",
    "CodexResponsesSessionInfo",
    "CodexResponsesTransportEvent",
    "CodexResponsesTransportObserver",
    "CodexResponsesTransportPhase",
)
