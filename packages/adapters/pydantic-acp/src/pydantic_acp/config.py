from __future__ import annotations as _annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ._version import __version__
from .approvals import ApprovalBridge, NativeApprovalBridge
from .bridges import CapabilityBridge
from .hook_projection import HookProjectionMap
from .models import AdapterModel
from .projection import DefaultToolClassifier, ProjectionMap, ToolClassifier
from .providers import (
    ApprovalStateProvider,
    ConfigOptionsProvider,
    NativePlanPersistenceProvider,
    PlanProvider,
    SessionModelsProvider,
    SessionModesProvider,
)
from .serialization import DefaultOutputSerializer, OutputSerializer
from .session.store import MemorySessionStore, SessionStore

DEFAULT_AGENT_NAME = "pydantic-acp"
DEFAULT_AGENT_TITLE = "Pydantic ACP"
DEFAULT_AGENT_VERSION = __version__

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
    capability_bridges: Sequence[CapabilityBridge] = field(default_factory=list)
    config_options_provider: ConfigOptionsProvider | None = None
    enable_generic_tool_projection: bool = True
    enable_model_config_option: bool = True
    hook_projection_map: HookProjectionMap | None = field(default_factory=HookProjectionMap)
    models_provider: SessionModelsProvider | None = None
    modes_provider: SessionModesProvider | None = None
    native_plan_persistence_provider: NativePlanPersistenceProvider | None = None
    plan_provider: PlanProvider | None = None
    replay_history_on_load: bool = True
    available_models: list[AdapterModel] = field(default_factory=list)
    session_store: SessionStore = field(default_factory=MemorySessionStore)
    output_serializer: OutputSerializer = field(default_factory=DefaultOutputSerializer)
    projection_maps: Sequence[ProjectionMap] = field(default_factory=tuple)
    tool_classifier: ToolClassifier = field(default_factory=DefaultToolClassifier)
