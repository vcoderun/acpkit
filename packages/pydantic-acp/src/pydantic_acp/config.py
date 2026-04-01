from __future__ import annotations as _annotations

from dataclasses import dataclass, field

from .approvals import ApprovalBridge, NativeApprovalBridge
from .bridges import CapabilityBridge
from .models import AdapterModel
from .projection import DefaultToolClassifier, ToolClassifier
from .providers import (
    ApprovalStateProvider,
    ConfigOptionsProvider,
    PlanProvider,
    SessionModelsProvider,
    SessionModesProvider,
)
from .serialization import DefaultOutputSerializer, OutputSerializer
from .session.store import MemorySessionStore, SessionStore

DEFAULT_AGENT_NAME = "pydantic-acp"
DEFAULT_AGENT_TITLE = "Pydantic ACP"
DEFAULT_AGENT_VERSION = "0.1.0"

__all__ = (
    "DEFAULT_AGENT_NAME",
    "DEFAULT_AGENT_TITLE",
    "DEFAULT_AGENT_VERSION",
    "AdapterConfig",
)


@dataclass(slots=True, kw_only=True)
class AdapterConfig:
    agent_name: str = DEFAULT_AGENT_NAME
    agent_title: str = DEFAULT_AGENT_TITLE
    agent_version: str = DEFAULT_AGENT_VERSION
    allow_model_selection: bool = False
    approval_bridge: ApprovalBridge | None = field(default_factory=NativeApprovalBridge)
    approval_state_provider: ApprovalStateProvider | None = None
    capability_bridges: list[CapabilityBridge] = field(default_factory=list)
    config_options_provider: ConfigOptionsProvider | None = None
    enable_generic_tool_projection: bool = True
    enable_model_config_option: bool = True
    models_provider: SessionModelsProvider | None = None
    modes_provider: SessionModesProvider | None = None
    plan_provider: PlanProvider | None = None
    replay_history_on_load: bool = True
    available_models: list[AdapterModel] = field(default_factory=list)
    session_store: SessionStore = field(default_factory=MemorySessionStore)
    output_serializer: OutputSerializer = field(default_factory=DefaultOutputSerializer)
    tool_classifier: ToolClassifier = field(default_factory=DefaultToolClassifier)
