from __future__ import annotations as _annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai.capabilities import Hooks

from ..agent_types import RuntimeAgent
from ..session.state import AcpSessionContext, JsonValue
from ._hook_capability import build_hook_capability, enabled_hook_events
from .base import BufferedCapabilityBridge

__all__ = ("HookBridge",)


@dataclass(slots=True)
class HookBridge(BufferedCapabilityBridge):
    metadata_key: str | None = "hooks"
    hide_all: bool = False
    record_event_stream: bool = True
    record_model_requests: bool = True
    record_node_lifecycle: bool = True
    record_prepare_tools: bool = True
    record_run_lifecycle: bool = True
    record_tool_execution: bool = True
    record_tool_validation: bool = True

    def build_capability(self, session: AcpSessionContext) -> Hooks[Any]:
        return build_hook_capability(self, session)

    def get_session_metadata(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> dict[str, JsonValue]:
        del session, agent
        events: list[JsonValue] = list(enabled_hook_events(self))
        return {"events": events}

    @property
    def _event_stream_enabled(self) -> bool:
        return not self.hide_all and self.record_event_stream

    @property
    def _model_requests_enabled(self) -> bool:
        return not self.hide_all and self.record_model_requests

    @property
    def _node_lifecycle_enabled(self) -> bool:
        return not self.hide_all and self.record_node_lifecycle

    @property
    def _prepare_tools_enabled(self) -> bool:
        return not self.hide_all and self.record_prepare_tools

    @property
    def _run_lifecycle_enabled(self) -> bool:
        return not self.hide_all and self.record_run_lifecycle

    @property
    def _tool_execution_enabled(self) -> bool:
        return not self.hide_all and self.record_tool_execution

    @property
    def _tool_validation_enabled(self) -> bool:
        return not self.hide_all and self.record_tool_validation
