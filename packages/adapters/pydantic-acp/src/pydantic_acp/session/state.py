from __future__ import annotations as _annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias, TypeVar, assert_never

from acp.exceptions import RequestError
from acp.schema import (
    AgentMessageChunk,
    ClientCapabilities,
    CreateElicitationResponse,
    ElicitationFormRequestMode,
    ElicitationFormSessionMode,
    ElicitationMode,
    ElicitationUrlRequestMode,
    ElicitationUrlSessionMode,
    SessionInfoUpdate,
    ToolCallProgress,
    ToolCallStart,
    UserMessageChunk,
)
from pydantic import BaseModel
from typing_extensions import TypeIs

from ..elicitation import (
    ChoiceElicitationFallback,
    ChoiceElicitationResult,
    ElicitationChoice,
    _ask_choice,
)

if TYPE_CHECKING:
    from acp.interfaces import Client as AcpClient

JsonPrimitive: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
SessionTranscriptUpdate: TypeAlias = (
    AgentMessageChunk | SessionInfoUpdate | ToolCallProgress | ToolCallStart | UserMessageChunk
)
SessionTranscriptKind: TypeAlias = Literal[
    "agent_message_chunk",
    "session_info_update",
    "tool_call",
    "tool_call_update",
    "user_message_chunk",
]

_SESSION_UPDATE_MODELS: dict[str, type[BaseModel]] = {
    "agent_message_chunk": AgentMessageChunk,
    "session_info_update": SessionInfoUpdate,
    "tool_call": ToolCallStart,
    "tool_call_update": ToolCallProgress,
    "user_message_chunk": UserMessageChunk,
}

_TRANSCRIPT_KINDS: Final = frozenset(_SESSION_UPDATE_MODELS)
ChoiceValueT = TypeVar("ChoiceValueT")


def _is_transcript_kind(value: JsonValue) -> TypeIs[SessionTranscriptKind]:
    return isinstance(value, str) and value in _TRANSCRIPT_KINDS


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True, kw_only=True)
class StoredSessionUpdate:
    kind: SessionTranscriptKind
    payload: dict[str, JsonValue]

    @classmethod
    def from_update(cls, update: SessionTranscriptUpdate) -> StoredSessionUpdate:
        payload = _coerce_json_object(
            update.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
        session_update = payload.get("sessionUpdate")
        if not _is_transcript_kind(session_update):
            raise TypeError("Session transcript update payload is missing `sessionUpdate`.")
        return cls(kind=session_update, payload=payload)

    def to_update(self) -> SessionTranscriptUpdate:
        if self.kind == "agent_message_chunk":
            return AgentMessageChunk.model_validate(self.payload)
        if self.kind == "session_info_update":
            return SessionInfoUpdate.model_validate(self.payload)
        if self.kind == "tool_call":
            return ToolCallStart.model_validate(self.payload)
        if self.kind == "tool_call_update":
            return ToolCallProgress.model_validate(self.payload)
        if self.kind == "user_message_chunk":
            return UserMessageChunk.model_validate(self.payload)
        assert_never(self.kind)


@dataclass(slots=True, kw_only=True)
class AcpSessionContext:
    session_id: str
    cwd: Path
    created_at: datetime
    updated_at: datetime
    additional_directories: tuple[Path, ...] = ()
    title: str | None = None
    session_model_id: str | None = None
    message_history_json: str | None = None
    plan_markdown: str | None = None
    plan_entries: list[dict[str, JsonValue]] = field(default_factory=list)
    active_plan_id: str | None = None
    config_values: dict[str, str | bool] = field(default_factory=dict)
    mcp_servers: list[dict[str, JsonValue]] = field(default_factory=list)
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    transcript: list[StoredSessionUpdate] = field(default_factory=list)
    client: AcpClient | None = field(default=None, repr=False, compare=False)
    client_capabilities: ClientCapabilities | None = field(default=None, repr=False, compare=False)

    def supports_config_options(self) -> bool:
        """Return whether the connected client accepts session config options."""
        capabilities = self.client_capabilities
        if capabilities is None:
            return True
        return capabilities.session is not None and capabilities.session.config_options is not None

    def supports_boolean_config_options(self) -> bool:
        """Return whether the connected client accepts boolean config options."""
        capabilities = self.client_capabilities
        if capabilities is None:
            return True
        session_capabilities = capabilities.session
        return (
            session_capabilities is not None
            and session_capabilities.config_options is not None
            and session_capabilities.config_options.boolean is not None
        )

    def supports_plan_content_updates(self) -> bool:
        """Return whether the client supports unstable plan delta updates."""
        capabilities = self.client_capabilities
        return capabilities is None or capabilities.plan is not None

    def supports_elicitation(self, mode: ElicitationMode) -> bool:
        capabilities = self.client_capabilities
        if capabilities is None or capabilities.elicitation is None:
            return False
        if isinstance(mode, ElicitationFormSessionMode | ElicitationFormRequestMode):
            return capabilities.elicitation.form is not None
        if isinstance(mode, ElicitationUrlSessionMode | ElicitationUrlRequestMode):
            return capabilities.elicitation.url is not None
        return False

    async def create_elicitation(
        self,
        message: str,
        mode: ElicitationMode,
    ) -> CreateElicitationResponse:
        if not self.supports_elicitation(mode):
            raise RequestError.invalid_request({"reason": "unsupported_elicitation_mode"})
        if self.client is None:
            raise RequestError.invalid_request({"reason": "client_not_connected"})
        return await self.client.create_elicitation(message=message, mode=mode)

    async def ask_choice(
        self,
        question: str,
        choices: Sequence[ElicitationChoice[ChoiceValueT]],
        *,
        fallback: ChoiceElicitationFallback[ChoiceValueT] | None = None,
    ) -> ChoiceElicitationResult[ChoiceValueT]:
        """Ask the connected client to select one typed value.

        The helper compiles choices to the standard ACP form-elicitation schema.
        Clients control presentation and may ignore option descriptions.
        """
        return await _ask_choice(self, question, choices, fallback=fallback)

    async def complete_elicitation(self, elicitation_id: str) -> None:
        if self.client is None:
            raise RequestError.invalid_request({"reason": "client_not_connected"})
        await self.client.complete_elicitation(elicitation_id=elicitation_id)


def _coerce_json_object(value: Any) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise TypeError("Expected a JSON object payload.")
    payload: dict[str, JsonValue] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError("JSON object keys must be strings.")
        payload[key] = _coerce_json_value(item)
    return payload


def _coerce_json_value(value: Any) -> JsonValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list):
        return [_coerce_json_value(item) for item in value]
    if isinstance(value, dict):
        return _coerce_json_object(value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


__all__ = (
    "AcpSessionContext",
    "JsonValue",
    "SessionTranscriptUpdate",
    "StoredSessionUpdate",
    "utc_now",
)
