from __future__ import annotations as _annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from acp.schema import McpCapabilities, ToolCallProgress, ToolCallStart, ToolCallStatus, ToolKind
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.settings import ModelSettings

from ..agent_types import RuntimeAgent
from ..providers import ConfigOption, ModeState
from ..session.state import AcpSessionContext, JsonValue, SessionTranscriptUpdate

__all__ = (
    "BufferedCapabilityBridge",
    "CapabilityBridge",
)


class CapabilityBridge:
    metadata_key: str | None = None

    def build_agent_capabilities(
        self,
        session: AcpSessionContext,
    ) -> tuple[AbstractCapability[Any], ...] | None:
        del session
        return None

    def drain_updates(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> list[SessionTranscriptUpdate] | None:
        del session, agent
        return None

    def get_config_options(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> list[ConfigOption] | None:
        del session, agent
        return None

    def get_mcp_capabilities(self, agent: RuntimeAgent | None = None) -> McpCapabilities | None:
        del agent
        return None

    def get_approval_policy_key(
        self,
        tool_name: str,
        raw_input: JsonValue | None = None,
    ) -> str | None:
        del tool_name, raw_input
        return None

    def get_mode_state(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> ModeState | None:
        del session, agent
        return None

    def get_model_settings(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> ModelSettings | None:
        del session, agent
        return None

    def get_session_metadata(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> dict[str, JsonValue] | None:
        del session, agent
        return None

    def get_tool_kind(self, tool_name: str, raw_input: JsonValue | None = None) -> ToolKind | None:
        del tool_name, raw_input
        return None

    def set_config_option(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
        config_id: str,
        value: str | bool,
    ) -> list[ConfigOption] | None:
        del session, agent, config_id, value
        return None

    def set_mode(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
        mode_id: str,
    ) -> ModeState | None:
        del session, agent, mode_id
        return None


@dataclass(slots=True)
class BufferedCapabilityBridge(CapabilityBridge):
    _event_counts: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _pending_updates: dict[str, list[SessionTranscriptUpdate]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def drain_updates(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> list[SessionTranscriptUpdate] | None:
        del agent
        pending = self._pending_updates.pop(session.session_id, None)
        return list(pending) if pending else None

    def _record_completed_event(
        self,
        session: AcpSessionContext,
        *,
        title: str,
        kind: ToolKind = "execute",
        raw_input: JsonValue | None = None,
        raw_output: str | None = None,
    ) -> None:
        event_id = self._record_started_event(
            session,
            title=title,
            kind=kind,
            raw_input=raw_input,
        )
        self._record_progress_event(
            session,
            event_id=event_id,
            title=title,
            kind=kind,
            status="completed",
            raw_output=raw_output,
        )

    def _record_failed_event(
        self,
        session: AcpSessionContext,
        *,
        title: str,
        kind: ToolKind = "execute",
        raw_input: JsonValue | None = None,
        raw_output: str | None = None,
    ) -> None:
        event_id = self._record_started_event(
            session,
            title=title,
            kind=kind,
            raw_input=raw_input,
        )
        self._record_progress_event(
            session,
            event_id=event_id,
            title=title,
            kind=kind,
            status="failed",
            raw_output=raw_output,
        )

    def _record_started_event(
        self,
        session: AcpSessionContext,
        *,
        title: str,
        kind: ToolKind = "execute",
        raw_input: JsonValue | None = None,
    ) -> str:
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
                )
            ],
        )
        return event_id

    def _record_progress_event(
        self,
        session: AcpSessionContext,
        *,
        event_id: str,
        title: str,
        status: ToolCallStatus,
        kind: ToolKind = "execute",
        raw_output: str | None = None,
    ) -> None:
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
