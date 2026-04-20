from __future__ import annotations as _annotations

from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
from typing import Any

from acp.schema import PlanEntry, ToolCallProgress, ToolCallStart, ToolCallStatus, ToolKind

from ..providers import ConfigOption, ModelSelectionState, ModeState
from ..session.state import AcpSessionContext, JsonValue, SessionTranscriptUpdate

__all__ = (
    "BufferedCapabilityBridge",
    "CapabilityBridge",
)


class CapabilityBridge:
    metadata_key: str | None = None

    def drain_updates(self, session: AcpSessionContext) -> list[SessionTranscriptUpdate] | None:
        del session
        return None

    def get_config_options(
        self,
        session: AcpSessionContext,
    ) -> list[ConfigOption] | None | Awaitable[list[ConfigOption] | None]:
        del session
        return None

    def get_mode_state(
        self,
        session: AcpSessionContext,
    ) -> ModeState | None | Awaitable[ModeState | None]:
        del session
        return None

    def get_model_state(
        self,
        session: AcpSessionContext,
    ) -> ModelSelectionState | None | Awaitable[ModelSelectionState | None]:
        del session
        return None

    def get_session_metadata(self, session: AcpSessionContext) -> dict[str, JsonValue] | None:
        del session
        return None

    def get_interrupt_configuration(
        self, session: AcpSessionContext
    ) -> dict[str, JsonValue] | None:
        del session
        return None

    def get_middleware(self, session: AcpSessionContext) -> tuple[Any, ...]:
        del session
        return ()

    def get_response_format(self, session: AcpSessionContext) -> Any:
        del session
        return None

    def get_system_prompt_parts(self, session: AcpSessionContext) -> tuple[str, ...]:
        del session
        return ()

    def get_tools(self, session: AcpSessionContext) -> tuple[Any, ...]:
        del session
        return ()

    def get_tool_kind(self, tool_name: str, raw_input: JsonValue | None = None) -> ToolKind | None:
        del tool_name, raw_input
        return None

    def get_approval_policy_key(
        self,
        tool_name: str,
        raw_input: JsonValue | None = None,
    ) -> str | None:
        del tool_name, raw_input
        return None

    def extract_plan_entries(self, payload: JsonValue) -> list[PlanEntry] | None:
        del payload
        return None

    def set_config_option(
        self,
        session: AcpSessionContext,
        config_id: str,
        value: str | bool,
    ) -> list[ConfigOption] | None | Awaitable[list[ConfigOption] | None]:
        del session, config_id, value
        return None

    def set_mode(
        self,
        session: AcpSessionContext,
        mode_id: str,
    ) -> ModeState | None | Awaitable[ModeState | None]:
        del session, mode_id
        return None

    def set_model(
        self,
        session: AcpSessionContext,
        model_id: str,
    ) -> ModelSelectionState | None | Awaitable[ModelSelectionState | None]:
        del session, model_id
        return None


@dataclass(slots=True)
class BufferedCapabilityBridge(CapabilityBridge):
    _event_counts: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _pending_updates: dict[str, list[SessionTranscriptUpdate]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def drain_updates(self, session: AcpSessionContext) -> list[SessionTranscriptUpdate] | None:
        pending = self._pending_updates.pop(session.session_id, None)
        return list(pending) if pending else None

    def _append_updates(
        self,
        session: AcpSessionContext,
        updates: Sequence[SessionTranscriptUpdate],
    ) -> None:
        self._pending_updates.setdefault(session.session_id, []).extend(updates)

    def _next_event_id(self, session: AcpSessionContext) -> str:
        next_count = self._event_counts.get(session.session_id, 0) + 1
        self._event_counts[session.session_id] = next_count
        bridge_name = type(self).__name__.replace("Bridge", "").lower() or "bridge"
        return f"{session.session_id}:{bridge_name}:{next_count}"

    def _record_completed_event(
        self,
        session: AcpSessionContext,
        *,
        title: str,
        kind: ToolKind = "execute",
        raw_input: JsonValue | None = None,
        raw_output: str | None = None,
    ) -> None:
        event_id = self._next_event_id(session)
        self._append_updates(
            session,
            [
                ToolCallStart(
                    session_update="tool_call",
                    tool_call_id=event_id,
                    title=title,
                    kind=kind,
                    status="in_progress",
                    raw_input=raw_input,
                ),
                ToolCallProgress(
                    session_update="tool_call_update",
                    tool_call_id=event_id,
                    title=title,
                    kind=kind,
                    status="completed",
                    raw_output=raw_output,
                ),
            ],
        )

    def _record_progress_event(
        self,
        session: AcpSessionContext,
        *,
        title: str,
        status: ToolCallStatus,
        kind: ToolKind = "execute",
        raw_output: str | None = None,
    ) -> None:
        event_id = self._next_event_id(session)
        self._append_updates(
            session,
            [
                ToolCallProgress(
                    session_update="tool_call_update",
                    tool_call_id=event_id,
                    title=title,
                    kind=kind,
                    status=status,
                    raw_output=raw_output,
                )
            ],
        )
