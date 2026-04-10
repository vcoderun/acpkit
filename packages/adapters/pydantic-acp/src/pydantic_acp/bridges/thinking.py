from __future__ import annotations as _annotations

from dataclasses import dataclass
from typing import Final, cast

from acp.schema import SessionConfigOptionSelect, SessionConfigSelectOption
from pydantic_ai.capabilities import Thinking
from pydantic_ai.settings import ModelSettings, ThinkingEffort

from ..agent_types import RuntimeAgent
from ..providers import ConfigOption
from ..session.state import AcpSessionContext, JsonValue
from .base import CapabilityBridge

__all__ = ("ThinkingBridge",)

_DEFAULT_THINKING_VALUE: Final[str] = "default"
_DISABLED_THINKING_VALUE: Final[str] = "off"
_THINKING_OPTIONS: Final[tuple[str, ...]] = (
    _DEFAULT_THINKING_VALUE,
    _DISABLED_THINKING_VALUE,
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
)
_THINKING_OPTION_LABELS: Final[dict[str, str]] = {
    _DEFAULT_THINKING_VALUE: "Default",
    _DISABLED_THINKING_VALUE: "Off",
    "minimal": "Minimal",
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "xhigh": "XHigh",
}


@dataclass(slots=True, frozen=True, kw_only=True)
class ThinkingBridge(CapabilityBridge):
    metadata_key = "thinking"

    config_id: str = "thinking"
    config_name: str = "Thinking Effort"
    config_description: str = "Session-local thinking/reasoning effort."

    def get_config_options(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> list[ConfigOption]:
        del agent
        return [
            SessionConfigOptionSelect(
                id=self.config_id,
                name=self.config_name,
                category="model",
                description=self.config_description,
                type="select",
                current_value=self._current_value(session),
                options=[
                    SessionConfigSelectOption(
                        value=value,
                        name=_THINKING_OPTION_LABELS[value],
                    )
                    for value in _THINKING_OPTIONS
                ],
            )
        ]

    def get_model_settings(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> ModelSettings | None:
        del agent
        current_value = self._current_value(session)
        if current_value == _DEFAULT_THINKING_VALUE:
            return None
        if current_value == _DISABLED_THINKING_VALUE:
            return Thinking(False).get_model_settings()
        effort = cast(ThinkingEffort, current_value)
        return Thinking(effort=effort).get_model_settings()

    def get_session_metadata(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> dict[str, JsonValue]:
        del agent
        return {
            "config_id": self.config_id,
            "current_value": self._current_value(session),
            "supported_values": list(_THINKING_OPTIONS),
        }

    def set_config_option(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
        config_id: str,
        value: str | bool,
    ) -> list[ConfigOption] | None:
        if config_id != self.config_id or not isinstance(value, str):
            return None
        if value not in _THINKING_OPTIONS:
            return None
        if value == _DEFAULT_THINKING_VALUE:
            session.config_values.pop(self.config_id, None)
        else:
            session.config_values[self.config_id] = value
        return self.get_config_options(session, agent)

    def _current_value(self, session: AcpSessionContext) -> str:
        value = session.config_values.get(self.config_id)
        if isinstance(value, str) and value in _THINKING_OPTIONS:
            return value
        return _DEFAULT_THINKING_VALUE
