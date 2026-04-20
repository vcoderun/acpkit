from __future__ import annotations as _annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from inspect import isawaitable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from acp.exceptions import RequestError
from acp.interfaces import Client as AcpClient
from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AudioContentBlock,
    BlobResourceContents,
    ContentToolCallContent,
    EmbeddedResourceContentBlock,
    FileEditToolCallContent,
    HttpMcpServer,
    ImageContentBlock,
    McpServerStdio,
    ModelInfo,
    PlanEntry,
    ResourceContentBlock,
    SessionConfigOptionBoolean,
    SessionInfoUpdate,
    SessionMode,
    SseMcpServer,
    TerminalToolCallContent,
    TextContentBlock,
    TextResourceContents,
    ToolCallLocation,
    ToolCallProgress,
    ToolCallStart,
    ToolKind,
    UserMessageChunk,
)
from langchain_acp import (
    AdapterConfig,
    BrowserProjectionMap,
    BufferedCapabilityBridge,
    CapabilityBridge,
    CommandProjectionMap,
    CommunityFileManagementProjectionMap,
    CompositeProjectionMap,
    ConfigOptionsBridge,
    DeepAgentsCompatibilityBridge,
    DeepAgentsProjectionMap,
    DefaultToolClassifier,
    FactoryGraphSource,
    FileSessionStore,
    FileSystemProjectionMap,
    FinanceProjectionMap,
    GraphBridgeBuilder,
    GraphBuildContributions,
    HttpRequestProjectionMap,
    MemorySessionStore,
    ModelSelectionBridge,
    ModeSelectionBridge,
    NativeApprovalBridge,
    StaticGraphSource,
    StructuredEventProjectionMap,
    TaskPlan,
    ToolSurfaceBridge,
    WebSearchProjectionMap,
    acp_get_plan,
    acp_mark_plan_done,
    acp_set_plan,
    acp_update_plan_entry,
    build_tool_progress_update,
    build_tool_start_update,
    compose_event_projection_maps,
    compose_projection_maps,
    create_acp_agent,
    extract_tool_call_locations,
)
from langchain_acp.approvals import ApprovalDecision
from langchain_acp.event_projection import (
    _event_payload_to_update,
    _extract_event_payloads,
    _normalize_text_content,
    _resolve_session_update_kind,
)
from langchain_acp.plan import _bind_native_plan_context
from langchain_acp.projection import (
    ToolProjection,
    _browser_action_title,
    _browser_progress_title,
    _browser_read_title,
    _browser_text_preview,
    _command_risk_note,
    _command_text,
    _command_title_from_input,
    _file_management_locations,
    _file_management_mutation_title,
    _finance_dataset_title,
    _finance_query,
    _first_string,
    _format_browser_element_results,
    _format_browser_link_results,
    _format_command_title,
    _format_web_fetch_progress,
    _format_web_fetch_start,
    _format_web_search_progress,
    _format_web_search_start,
    _http_method_label,
    _normalize_search_result_row,
    _normalized_search_results,
    _output_text,
    _parse_structured_value,
    _search_result_rows,
    _search_title,
    _terminal_id,
    _tool_title,
    _truncate_text,
    _web_fetch_url,
    _web_search_query,
)
from langchain_acp.providers import ConfigOption, ModelSelectionState, ModeState
from langchain_acp.runtime._native_plan_runtime import _NativePlanRuntime
from langchain_acp.runtime._prompt_conversion import (
    _embedded_resource_content,
    message_text,
    prompt_to_langchain_content,
)
from langchain_acp.runtime.adapter import LangChainAcpAgent
from langchain_acp.runtime.server import _resolve_config, _resolve_graph_source, run_acp
from langchain_acp.serialization import DefaultOutputSerializer, _json_compatible
from langchain_acp.session.state import (
    AcpSessionContext,
    StoredSessionUpdate,
    _coerce_json_object,
    _coerce_json_value,
    utc_now,
)
from langchain_acp.session.store import _store_lock
from langchain_core.messages import AIMessageChunk, ToolMessage
from langgraph.types import Command
from pydantic import BaseModel

from .support import GenericFakeChatModel, RecordingACPClient, agent_message_texts


@dataclass(kw_only=True)
class _DataclassPayload:
    name: str
    size: int


class _ModelPayload(BaseModel):
    name: str
    enabled: bool


@dataclass(slots=True, frozen=True, kw_only=True)
class _StaticProjectionMap:
    start_projection: ToolProjection | None = None
    progress_projection: ToolProjection | None = None

    def project_start(
        self,
        tool_name: str,
        *,
        cwd: Path | None = None,
        raw_input: Any = None,
    ) -> ToolProjection | None:
        del tool_name, cwd, raw_input
        return self.start_projection

    def project_progress(
        self,
        tool_name: str,
        *,
        cwd: Path | None = None,
        raw_input: Any = None,
        raw_output: Any = None,
        serialized_output: str,
        status: str,
    ) -> ToolProjection | None:
        del tool_name, cwd, raw_input, raw_output, serialized_output, status
        return self.progress_projection


@dataclass(slots=True)
class _BuilderGraph:
    name: str = "demo-graph"
    checkpointer: Any = None
    compile_calls: list[tuple[Any, str | None]] = field(default_factory=list)
    builder: Any = field(init=False)

    def __post_init__(self) -> None:
        self.builder = self

    def compile(self, *, checkpointer: Any, name: str | None = None) -> dict[str, Any]:
        self.compile_calls.append((checkpointer, name))
        return {"checkpointer": checkpointer, "name": name}


@dataclass(slots=True)
class _BuilderWithoutCompileGraph:
    checkpointer: Any = None
    builder: Any = field(default_factory=object)


@dataclass(slots=True, kw_only=True)
class _FixedApprovalBridge:
    decisions: list[dict[str, Any]] = field(default_factory=list)
    cancelled: bool = False
    calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = field(default_factory=list)

    async def resolve_action_requests(
        self,
        *,
        client: AcpClient,
        session: AcpSessionContext,
        action_requests: list[dict[str, Any]],
        review_configs: list[dict[str, Any]],
        classifier: Any,
    ) -> ApprovalDecision:
        del client, session, classifier
        self.calls.append((action_requests, review_configs))
        return ApprovalDecision(decisions=list(self.decisions), cancelled=self.cancelled)


@dataclass(slots=True, kw_only=True)
class _StreamingGraph:
    streams: list[list[Any]]
    inputs: list[Any] = field(default_factory=list)

    async def astream(
        self,
        stream_input: Any,
        *,
        config: Any,
        stream_mode: Any,
        subgraphs: bool,
    ):
        del config, stream_mode, subgraphs
        self.inputs.append(stream_input)
        for item in self.streams.pop(0):
            yield item


@dataclass(slots=True, kw_only=True)
class _SyncModelsProvider:
    model_state: ModelSelectionState | None
    set_calls: list[str] = field(default_factory=list)

    def get_model_state(self, session: AcpSessionContext) -> ModelSelectionState | None:
        del session
        return self.model_state

    def set_model(self, session: AcpSessionContext, model_id: str) -> ModelSelectionState | None:
        self.set_calls.append(model_id)
        if self.model_state is None:
            return None
        session.config_values["provider-model"] = model_id
        self.model_state = ModelSelectionState(
            available_models=list(self.model_state.available_models),
            current_model_id=model_id,
            allow_any_model_id=self.model_state.allow_any_model_id,
            enable_config_option=self.model_state.enable_config_option,
            config_option_name=self.model_state.config_option_name,
        )
        return self.model_state


@dataclass(slots=True, kw_only=True)
class _AsyncModesProvider:
    mode_state: ModeState | None
    set_calls: list[str] = field(default_factory=list)

    async def get_mode_state(self, session: AcpSessionContext) -> ModeState | None:
        del session
        return self.mode_state

    async def set_mode(self, session: AcpSessionContext, mode_id: str) -> ModeState | None:
        self.set_calls.append(mode_id)
        if self.mode_state is None:
            return None
        session.config_values["provider-mode"] = mode_id
        self.mode_state = ModeState(
            modes=list(self.mode_state.modes),
            current_mode_id=mode_id,
            enable_config_option=self.mode_state.enable_config_option,
            config_option_name=self.mode_state.config_option_name,
        )
        return self.mode_state


@dataclass(slots=True, kw_only=True)
class _ConfigProvider:
    options: list[ConfigOption]
    set_calls: list[tuple[str, str | bool]] = field(default_factory=list)

    async def get_config_options(self, session: AcpSessionContext) -> list[ConfigOption]:
        del session
        return list(self.options)

    async def set_config_option(
        self,
        session: AcpSessionContext,
        config_id: str,
        value: str | bool,
    ) -> list[ConfigOption]:
        self.set_calls.append((config_id, value))
        session.config_values[config_id] = value
        return list(self.options)


@dataclass(slots=True, kw_only=True)
class _PlanPersistenceProvider:
    calls: list[tuple[list[PlanEntry], str | None]] = field(default_factory=list)

    async def persist_plan_state(
        self,
        session: AcpSessionContext,
        *,
        entries: Sequence[PlanEntry],
        plan_markdown: str | None,
    ) -> None:
        del session
        self.calls.append((list(entries), plan_markdown))


@dataclass(slots=True, kw_only=True)
class _Phase5StateBridge(BufferedCapabilityBridge):
    metadata_key: str | None = "custom"
    models: list[ModelInfo] = field(
        default_factory=lambda: [
            ModelInfo(model_id="bridge-model", name="Bridge Model"),
            ModelInfo(model_id="pro", name="Pro"),
        ]
    )
    modes: list[SessionMode] = field(
        default_factory=lambda: [
            SessionMode(id="ask", name="Ask"),
            SessionMode(id="plan", name="Plan"),
        ]
    )

    def get_model_state(self, session: AcpSessionContext) -> ModelSelectionState:
        return ModelSelectionState(
            available_models=list(self.models),
            current_model_id=session.session_model_id or self.models[0].model_id,
            enable_config_option=False,
        )

    def set_model(self, session: AcpSessionContext, model_id: str) -> ModelSelectionState | None:
        if not any(model.model_id == model_id for model in self.models):
            return None
        session.session_model_id = model_id
        self._record_completed_event(session, title=f"model:{model_id}", kind="other")
        return self.get_model_state(session)

    def get_mode_state(self, session: AcpSessionContext) -> ModeState:
        return ModeState(
            modes=list(self.modes),
            current_mode_id=session.session_mode_id or self.modes[0].id,
            enable_config_option=False,
        )

    def set_mode(self, session: AcpSessionContext, mode_id: str) -> ModeState | None:
        if not any(mode.id == mode_id for mode in self.modes):
            return None
        session.session_mode_id = mode_id
        self._record_completed_event(session, title=f"mode:{mode_id}", kind="other")
        return self.get_mode_state(session)

    def get_config_options(self, session: AcpSessionContext) -> list[ConfigOption]:
        return [
            SessionConfigOptionBoolean(
                id="bridge_flag",
                name="Bridge Flag",
                type="boolean",
                current_value=bool(session.config_values.get("bridge_flag", False)),
            )
        ]

    def set_config_option(
        self,
        session: AcpSessionContext,
        config_id: str,
        value: str | bool,
    ) -> list[ConfigOption] | None:
        if config_id != "bridge_flag":
            return None
        session.config_values[config_id] = value
        self._record_completed_event(
            session,
            title="bridge_flag",
            kind="other",
            raw_output=str(value),
        )
        return self.get_config_options(session)

    def get_session_metadata(self, session: AcpSessionContext) -> dict[str, Any]:
        return {
            "mode": session.session_mode_id,
            "model": session.session_model_id,
        }


@dataclass(slots=True, frozen=True, kw_only=True)
class _UnknownPromptBlock:
    payload: str

    def model_dump(self, *, mode: str = "json") -> dict[str, str]:
        del mode
        return {"payload": self.payload}


def _make_session(*, session_id: str = "session-1", cwd: Path | None = None) -> AcpSessionContext:
    created_at = utc_now()
    return AcpSessionContext(
        session_id=session_id,
        cwd=cwd or Path("/tmp/demo"),
        created_at=created_at,
        updated_at=created_at,
    )


def _make_adapter(*, config: AdapterConfig | None = None) -> LangChainAcpAgent:
    return LangChainAcpAgent(
        StaticGraphSource(graph=cast(Any, object())),
        config=config or AdapterConfig(),
    )


async def _await_value(value: Any) -> Any:
    if isawaitable(value):
        return await value
    return value


def test_demo_bridge_invalid_inputs_and_helper_fallbacks() -> None:
    bridge = _Phase5StateBridge()
    session = _make_session()

    assert bridge.set_model(session, "missing-model") is None
    assert bridge.set_mode(session, "missing-mode") is None
    assert bridge.set_config_option(session, "missing-flag", True) is None
    assert asyncio.run(_await_value("value")) == "value"


def test_native_approval_bridge_handles_success_cancel_and_invalid_paths() -> None:
    bridge = NativeApprovalBridge()
    classifier = DefaultToolClassifier()
    client = RecordingACPClient()
    client.queue_permission_selected("allow_once")
    client.queue_permission_selected("reject_once")

    session = _make_session()
    decision = asyncio.run(
        bridge.resolve_action_requests(
            client=cast(AcpClient, client),
            session=session,
            action_requests=[
                {"name": "read_file", "args": {"path": "notes.txt"}},
                {"name": "delete_file", "args": {"path": "draft.txt"}},
            ],
            review_configs=[
                {
                    "action_name": "read_file",
                    "allowed_decisions": ["approve", "reject"],
                },
                {
                    "action_name": "delete_file",
                    "allowed_decisions": ["approve", "reject"],
                },
            ],
            classifier=classifier,
        )
    )

    assert decision == ApprovalDecision(decisions=[{"type": "approve"}, {"type": "reject"}])
    assert [request[2].kind for request in client.permission_requests] == [
        "read",
        "other",
    ]

    client = RecordingACPClient()
    client.queue_permission_cancelled()
    cancelled = asyncio.run(
        bridge.resolve_action_requests(
            client=cast(AcpClient, client),
            session=session,
            action_requests=[{"name": "execute_shell", "args": {"command": "pwd"}}],
            review_configs=[{"action_name": "execute_shell", "allowed_decisions": ["approve"]}],
            classifier=classifier,
        )
    )
    assert cancelled.cancelled is True

    with pytest.raises(RequestError):
        asyncio.run(
            bridge.resolve_action_requests(
                client=cast(AcpClient, RecordingACPClient()),
                session=session,
                action_requests=[cast(dict[str, Any], "bad")],
                review_configs=[],
                classifier=classifier,
            )
        )

    with pytest.raises(RequestError):
        asyncio.run(
            bridge.resolve_action_requests(
                client=cast(AcpClient, RecordingACPClient()),
                session=session,
                action_requests=[{"name": "write_file", "args": {"path": "a.txt"}}],
                review_configs=[{"action_name": "write_file", "allowed_decisions": ["edit"]}],
                classifier=classifier,
            )
        )

    client = RecordingACPClient()
    client.queue_permission_selected("unexpected")
    with pytest.raises(RequestError):
        asyncio.run(
            bridge.resolve_action_requests(
                client=cast(AcpClient, client),
                session=session,
                action_requests=[{"name": "read_file", "args": {"path": "notes.txt"}}],
                review_configs=[],
                classifier=classifier,
            )
        )

    with pytest.raises(RequestError):
        asyncio.run(
            bridge.resolve_action_requests(
                client=cast(AcpClient, RecordingACPClient()),
                session=session,
                action_requests=[{"name": "read_file", "args": "bad"}],
                review_configs=[],
                classifier=classifier,
            )
        )

    with pytest.raises(RequestError):
        asyncio.run(
            bridge.resolve_action_requests(
                client=cast(AcpClient, RecordingACPClient()),
                session=session,
                action_requests=[{"name": 1, "args": {"path": "notes.txt"}}],
                review_configs=[],
                classifier=classifier,
            )
        )


def test_phase5_builtin_bridges_cover_direct_paths(tmp_path: Path) -> None:
    session = _make_session(cwd=tmp_path)
    session.config_values["plan_generation_type"] = "tools"
    tool_kinds: dict[str, ToolKind] = {"dangerous_delete": "execute"}

    tool_bridge = ToolSurfaceBridge(
        tool_kinds=tool_kinds,
        approval_policy_keys={"dangerous_delete": "dangerous-policy"},
    )
    assert tool_bridge.get_tool_kind("dangerous_delete") == "execute"
    assert tool_bridge.get_approval_policy_key("dangerous_delete") == "dangerous-policy"

    model_bridge = ModelSelectionBridge(
        available_models=(
            ModelInfo(model_id="base", name="Base"),
            ModelInfo(model_id="pro", name="Pro"),
        )
    )
    assert model_bridge.get_model_state(session) is not None
    assert model_bridge.set_model(session, "missing") is None
    assert model_bridge.set_model(session, "pro") is not None
    assert session.session_model_id == "pro"

    mode_bridge = ModeSelectionBridge(
        available_modes=(
            SessionMode(id="ask", name="Ask"),
            SessionMode(id="plan", name="Plan"),
        )
    )
    assert mode_bridge.get_mode_state(session) is not None
    assert mode_bridge.set_mode(session, "missing") is None
    assert mode_bridge.set_mode(session, "plan") is not None
    assert session.session_mode_id == "plan"

    config_provider = _ConfigProvider(
        options=[
            SessionConfigOptionBoolean(
                id="safe_tools",
                name="Safe Tools",
                type="boolean",
                current_value=True,
            )
        ]
    )
    config_bridge = ConfigOptionsBridge(provider=config_provider)
    assert asyncio.run(_await_value(config_bridge.get_config_options(session))) is not None
    assert (
        asyncio.run(_await_value(config_bridge.set_config_option(session, "safe_tools", False)))
        is not None
    )

    deepagents_bridge = DeepAgentsCompatibilityBridge()
    deepagents_metadata = deepagents_bridge.get_session_metadata(session)
    assert deepagents_metadata is not None
    assert deepagents_metadata["cwd"] == str(tmp_path)
    assert deepagents_metadata["plan_generation_type"] == "tools"
    assert deepagents_bridge.extract_plan_entries(
        {"todos": [{"content": "Inspect repo", "status": "pending", "priority": "high"}]}
    ) == [PlanEntry(content="Inspect repo", status="pending", priority="high")]
    assert deepagents_bridge.extract_plan_entries(
        {
            "todos": [
                "bad",
                {"content": 1},
                {"content": "Bad status", "status": "???", "priority": "???"},
            ]
        }
    ) == [PlanEntry(content="Bad status", status="pending", priority="medium")]

    @dataclass(slots=True, kw_only=True)
    class _RecordingBridge(BufferedCapabilityBridge):
        def record(self, session: AcpSessionContext) -> None:
            self._append_updates(
                session,
                [
                    AgentMessageChunk(
                        session_update="agent_message_chunk",
                        content=TextContentBlock(type="text", text="buffered"),
                    )
                ],
            )
            self._record_completed_event(session, title="buffered-complete", kind="other")
            self._record_progress_event(
                session,
                title="buffered-progress",
                status="failed",
                kind="other",
                raw_output="nope",
            )

    recording_bridge = _RecordingBridge()
    recording_bridge.record(session)
    drained = recording_bridge.drain_updates(session)
    assert drained is not None
    assert len(drained) == 4
    assert recording_bridge.drain_updates(session) is None
    assert GraphBuildContributions(metadata={"x": "y"}).metadata["x"] == "y"


def test_phase5_graph_bridge_builder_and_manager_cover_custom_and_builtin_paths(
    tmp_path: Path,
) -> None:
    session = _make_session(cwd=tmp_path)
    session.session_model_id = "base"
    session.session_mode_id = "ask"
    session.config_values["plan_generation_type"] = "tools"
    config_provider = _ConfigProvider(
        options=[
            SessionConfigOptionBoolean(
                id="safe_tools",
                name="Safe Tools",
                type="boolean",
                current_value=True,
            )
        ]
    )

    @dataclass(slots=True, kw_only=True)
    class _ContributionBridge(CapabilityBridge):
        def get_middleware(self, session: AcpSessionContext) -> tuple[Any, ...]:
            del session
            return ("middleware",)

        def get_tools(self, session: AcpSessionContext) -> tuple[Any, ...]:
            del session
            return ("tool",)

        def get_system_prompt_parts(self, session: AcpSessionContext) -> tuple[str, ...]:
            del session
            return ("System prompt.",)

        def get_response_format(self, session: AcpSessionContext) -> Any:
            del session
            return {"kind": "structured"}

        def get_interrupt_configuration(self, session: AcpSessionContext) -> dict[str, Any] | None:
            del session
            return {"before": ["tools"]}

    builder = GraphBridgeBuilder.from_config(
        AdapterConfig(
            available_models=[ModelInfo(model_id="base", name="Base")],
            available_modes=[SessionMode(id="ask", name="Ask")],
            capability_bridges=[
                _ContributionBridge(),
                cast(
                    CapabilityBridge,
                    ToolSurfaceBridge(
                        tool_kinds={"shell_exec": cast(ToolKind, "execute")},
                        approval_policy_keys={"shell_exec": "shell-policy"},
                    ),
                ),
            ],
            config_options_provider=config_provider,
        )
    )
    manager = builder.build_manager()

    assert "deepagents" in manager.metadata_keys
    assert manager.tool_classifier.classify("shell_exec") == "execute"
    assert manager.tool_classifier.approval_policy_key("shell_exec") == "shell-policy"
    assert asyncio.run(manager.get_model_state(session)) is not None
    assert asyncio.run(manager.get_mode_state(session)) is not None
    assert asyncio.run(manager.get_config_options(session)) is not None
    assert asyncio.run(manager.set_config_option(session, "safe_tools", False)) is not None
    metadata_sections = manager.get_metadata_sections(session)
    assert metadata_sections["deepagents"] is not None
    assert cast(dict[str, Any], metadata_sections["deepagents"])["cwd"] == str(tmp_path)
    contributions = builder.build_graph_contributions(session)
    deepagents_metadata = cast(dict[str, Any], contributions.metadata["deepagents"])
    assert deepagents_metadata["cwd"] == str(tmp_path)
    assert deepagents_metadata["plan_generation_type"] == "tools"
    assert contributions.middleware == ("middleware",)
    assert contributions.tools == ("tool",)
    assert contributions.system_prompt_parts == ("System prompt.",)
    assert contributions.response_format == {"kind": "structured"}
    assert contributions.interrupt_configuration == {"before": ["tools"]}


def test_phase5_runtime_uses_custom_capability_bridges_for_state_metadata_and_updates(
    tmp_path: Path,
) -> None:
    captured_metadata: list[dict[str, Any]] = []

    def graph_factory(session: AcpSessionContext) -> Any:
        captured_metadata.append(dict(session.metadata))
        return _StreamingGraph(
            streams=[
                [
                    (
                        (),
                        "messages",
                        (AIMessageChunk(content="Bridge graph ready."), {}),
                    )
                ]
            ]
        )

    bridge = _Phase5StateBridge()
    adapter = cast(
        LangChainAcpAgent,
        create_acp_agent(
            graph_factory=graph_factory,
            config=AdapterConfig(capability_bridges=[bridge]),
        ),
    )
    client = RecordingACPClient()
    adapter.on_connect(cast(AcpClient, client))

    created = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    assert created.models is not None
    assert created.models.current_model_id == "bridge-model"
    assert created.modes is not None
    assert created.modes.current_mode_id == "ask"
    assert created.config_options is not None
    assert [option.id for option in created.config_options] == ["bridge_flag"]

    session = adapter._require_session(created.session_id)
    custom_metadata = cast(dict[str, Any], session.metadata["custom"])
    deepagents_metadata = cast(dict[str, Any], session.metadata["deepagents"])
    assert custom_metadata["model"] == "bridge-model"
    assert deepagents_metadata["cwd"] == str(tmp_path)

    assert asyncio.run(adapter.set_session_model("pro", session_id=created.session_id)) is not None
    assert asyncio.run(adapter.set_session_mode("plan", session_id=created.session_id)) is not None
    config_response = asyncio.run(
        adapter.set_config_option("bridge_flag", session_id=created.session_id, value=True)
    )
    assert config_response is not None

    updated_session = adapter._require_session(created.session_id)
    updated_custom_metadata = cast(dict[str, Any], updated_session.metadata["custom"])
    assert updated_custom_metadata["model"] == "pro"
    assert updated_custom_metadata["mode"] == "plan"
    metadata_sections = (
        GraphBridgeBuilder.from_config(AdapterConfig(capability_bridges=[bridge]))
        .build_manager()
        .get_metadata_sections(updated_session)
    )
    assert cast(dict[str, Any], metadata_sections["custom"])["model"] == "pro"
    assert cast(dict[str, Any], metadata_sections["deepagents"])["cwd"] == str(tmp_path)
    buffered_updates = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallProgress | ToolCallStart)
    ]
    assert len(buffered_updates) >= 3

    prompt_response = asyncio.run(
        adapter.prompt(
            prompt=[TextContentBlock(type="text", text="hello")],
            session_id=created.session_id,
        )
    )
    assert prompt_response.stop_reason == "end_turn"
    assert captured_metadata[-1]["custom"]["model"] == "pro"
    assert captured_metadata[-1]["custom"]["mode"] == "plan"
    assert agent_message_texts(client)[-1] == "Bridge graph ready."


def test_phase5_bridge_defaults_and_adapter_edges_cover_remaining_paths(
    tmp_path: Path,
) -> None:
    session = _make_session(cwd=tmp_path)
    base_bridge = CapabilityBridge()
    assert base_bridge.get_session_metadata(session) is None
    assert base_bridge.get_approval_policy_key("read_file") is None
    assert ModelSelectionBridge()._default_model_id() is None
    assert ModeSelectionBridge()._default_mode_id() is None

    @dataclass(slots=True, kw_only=True)
    class _AsyncStateBridge(CapabilityBridge):
        async def get_model_state(self, session: AcpSessionContext) -> ModelSelectionState:
            del session
            return ModelSelectionState(
                available_models=[ModelInfo(model_id="async-model", name="Async Model")],
                current_model_id="async-model",
                enable_config_option=False,
            )

        async def get_mode_state(self, session: AcpSessionContext) -> ModeState:
            del session
            return ModeState(
                modes=[SessionMode(id="async-mode", name="Async Mode")],
                current_mode_id="async-mode",
                enable_config_option=False,
            )

    async_manager = GraphBridgeBuilder.from_config(
        AdapterConfig(capability_bridges=[_AsyncStateBridge()])
    ).build_manager()
    assert asyncio.run(async_manager.get_model_state(session)) is not None
    assert asyncio.run(async_manager.get_mode_state(session)) is not None
    assert async_manager.tool_classifier.approval_policy_key("read_file") == "read_file"

    @dataclass(slots=True, kw_only=True)
    class _NoneMetadataBridge(CapabilityBridge):
        metadata_key: str | None = "none"

    metadata_manager = GraphBridgeBuilder.from_config(
        AdapterConfig(capability_bridges=[_NoneMetadataBridge(), _Phase5StateBridge()])
    ).build_manager()
    metadata_sections = metadata_manager.get_metadata_sections(session)
    assert "none" not in metadata_sections
    assert cast(dict[str, Any], metadata_sections["custom"])["model"] == "async-model"

    @dataclass(slots=True, kw_only=True)
    class _NoCurrentSelectionBridge(CapabilityBridge):
        def get_model_state(self, session: AcpSessionContext) -> ModelSelectionState:
            del session
            return ModelSelectionState(
                available_models=[ModelInfo(model_id="ghost", name="Ghost")],
                current_model_id=None,
            )

        def get_mode_state(self, session: AcpSessionContext) -> ModeState:
            del session
            return ModeState(
                modes=[SessionMode(id="ghost", name="Ghost")],
                current_mode_id=None,
            )

    adapter = _make_adapter(
        config=AdapterConfig(
            available_models=[ModelInfo(model_id="base", name="Base")],
            available_modes=[SessionMode(id="ask", name="Ask")],
            capability_bridges=[_NoCurrentSelectionBridge()],
        )
    )
    assert adapter._model_exists("base") is True
    assert adapter._model_exists("missing") is False
    assert adapter._mode_exists("ask") is True
    assert adapter._mode_exists("missing") is False
    assert (
        asyncio.run(adapter._config_options(_make_session(cwd=tmp_path, session_id="none-current")))
        == []
    )
    assert asyncio.run(adapter._await_maybe("sync-value")) == "sync-value"


def test_projection_helpers_cover_classification_composition_and_locations() -> None:
    classifier = DefaultToolClassifier()
    assert classifier.classify("list_files") == "read"
    assert classifier.classify("write_file") == "edit"
    assert classifier.classify("execute_shell") == "execute"
    assert classifier.classify("search_docs") == "search"
    assert classifier.classify("grep") == "search"
    assert classifier.classify("duckduckgo_results_json") == "search"
    assert classifier.classify("requests_get") == "fetch"
    assert classifier.classify("terminal") == "execute"
    assert classifier.classify("unknown_tool") == "other"
    assert classifier.approval_policy_key("read_file") == "read_file"

    projection_map = FileSystemProjectionMap(
        write_tool_names=frozenset({"write_file"}),
        read_tool_names=frozenset({"read_file"}),
        execute_tool_names=frozenset({"execute_shell"}),
    )

    execute_start = projection_map.project_start(
        "execute_shell",
        raw_input={"command": "echo hello " * 20},
    )
    assert execute_start is not None
    assert execute_start.title is not None
    assert execute_start.title.endswith("…")
    risky_start = projection_map.project_start(
        "execute_shell",
        raw_input={"command": "rm -rf build"},
    )
    assert risky_start is not None
    assert risky_start.content is not None
    assert len(risky_start.content) == 2

    write_start = projection_map.project_start(
        "write_file",
        raw_input={"path": "draft.txt", "old_string": "old", "new_string": "new text"},
    )
    assert write_start is not None
    assert write_start.locations == [ToolCallLocation(path="draft.txt")]
    assert write_start.content is not None
    assert isinstance(write_start.content[0], FileEditToolCallContent)
    assert write_start.content[0].old_text == "old"
    assert write_start.title == "Write `draft.txt`"

    read_start = projection_map.project_start("read_file", raw_input={"path": "notes.txt"})
    assert read_start is not None
    assert read_start.locations == [ToolCallLocation(path="notes.txt")]
    assert read_start.title == "Read `notes.txt`"
    search_projection_map = FileSystemProjectionMap(search_tool_names=frozenset({"glob", "ls"}))
    search_start = search_projection_map.project_start(
        "glob",
        raw_input={"pattern": "*.py", "path": "src"},
    )
    assert search_start is not None
    assert search_start.title == "Glob `*.py`"
    assert projection_map.project_start("other_tool", raw_input={}) is None
    assert projection_map.project_start("read_file", raw_input="bad") is None
    assert projection_map.project_start("execute_shell", raw_input={"path": "notes.txt"}) is None
    assert projection_map.project_start("write_file", raw_input={"path": "draft.txt"}) is None
    assert projection_map.project_start("read_file", raw_input={"content": "missing path"}) is None
    blank_command_start = projection_map.project_start("execute_shell", raw_input={"command": " "})
    assert blank_command_start is not None
    assert blank_command_start.title == "command"

    read_progress = projection_map.project_progress(
        "read_file",
        raw_input={"path": "notes.txt"},
        raw_output="ignored",
        serialized_output="file body",
        status="completed",
    )
    assert read_progress is not None
    assert read_progress.locations == [ToolCallLocation(path="notes.txt")]
    assert (
        projection_map.project_progress(
            "read_file",
            raw_input={"content": "missing path"},
            raw_output=None,
            serialized_output="file body",
            status="completed",
        )
        is None
    )

    execute_progress = projection_map.project_progress(
        "execute_shell",
        raw_input={"command": "pwd"},
        raw_output={"terminal_id": "term-1", "stdout": "pwd output"},
        serialized_output="pwd output",
        status="completed",
    )
    assert execute_progress is not None
    assert execute_progress.content is not None
    assert isinstance(execute_progress.content[0], TerminalToolCallContent)
    assert execute_progress.content[0].terminal_id == "term-1"
    assert execute_progress.title == "pwd"
    assert (
        projection_map.project_progress(
            "other_tool",
            raw_input={},
            raw_output=None,
            serialized_output="ignored",
            status="completed",
        )
        is None
    )
    assert (
        projection_map.project_progress(
            "execute_shell",
            raw_input={"command": "pwd"},
            raw_output=None,
            serialized_output="pwd output",
            status="in_progress",
        )
        is None
    )

    start_update = build_tool_start_update(
        tool_call_id="call-1",
        tool_name="write_file",
        classifier=classifier,
        raw_input={"path": "draft.txt", "content": "hello"},
        cwd=None,
        projection_map=projection_map,
    )
    assert start_update.title == "Write `draft.txt`"
    assert start_update.content is not None

    progress_update = build_tool_progress_update(
        tool_call_id="call-1",
        tool_name="other_tool",
        classifier=classifier,
        raw_input={"path": "draft.txt"},
        raw_output={"ok": True},
        serialized_output="fallback output",
        cwd=None,
        projection_map=None,
        status="completed",
    )
    assert progress_update.content is not None
    assert progress_update.locations == [ToolCallLocation(path="draft.txt")]

    merged_progress = build_tool_progress_update(
        tool_call_id="ignored",
        tool_name="other_tool",
        classifier=classifier,
        raw_input={},
        raw_output=None,
        serialized_output="merged",
        cwd=None,
        projection_map=None,
        status="completed",
    )
    assert merged_progress.content is not None

    merged = CompositeProjectionMap(
        maps=(
            _StaticProjectionMap(
                start_projection=ToolProjection(
                    title="Tool A",
                    locations=[ToolCallLocation(path="a.txt")],
                )
            ),
            _StaticProjectionMap(
                start_projection=ToolProjection(
                    content=[cast(Any, merged_progress.content[0])],
                    status="completed",
                )
            ),
        )
    ).project_start("other_tool", raw_input={})
    assert merged is not None
    assert merged.title == "Tool A"
    assert merged.status == "completed"
    merged_progress_projection = CompositeProjectionMap(
        maps=(
            _StaticProjectionMap(progress_projection=ToolProjection(title="Progress A")),
            _StaticProjectionMap(progress_projection=ToolProjection(status="failed")),
        )
    ).project_progress("tool", serialized_output="done")
    assert merged_progress_projection is not None
    assert merged_progress_projection.title == "Progress A"
    assert merged_progress_projection.status == "failed"
    assert CompositeProjectionMap(maps=(_StaticProjectionMap(),)).project_start("tool") is None
    assert compose_projection_maps(None) is None
    assert compose_projection_maps(()) is None
    assert compose_projection_maps((projection_map,)) is projection_map
    composite_map = compose_projection_maps((projection_map, projection_map))
    assert isinstance(composite_map, CompositeProjectionMap)
    assert extract_tool_call_locations({"path": "draft.txt"}) == [
        ToolCallLocation(path="draft.txt")
    ]
    assert extract_tool_call_locations({"nope": "value"}) == []
    assert extract_tool_call_locations("bad") == []

    deepagents_map = DeepAgentsProjectionMap()
    deepagents_start = deepagents_map.project_start(
        "edit_file",
        raw_input={
            "file_path": "notes.txt",
            "old_string": "old body",
            "new_string": "new body",
        },
    )
    assert deepagents_start is not None
    assert deepagents_start.title == "Edit `notes.txt`"
    assert deepagents_start.content is not None
    assert isinstance(deepagents_start.content[0], FileEditToolCallContent)
    assert deepagents_start.content[0].old_text == "old body"
    deepagents_progress = deepagents_map.project_progress(
        "execute",
        raw_input={"command": "pwd"},
        raw_output={},
        serialized_output="",
        status="completed",
    )
    assert deepagents_progress is not None
    assert deepagents_progress.content is None


def test_web_projection_maps_render_search_and_fetch_results() -> None:
    search_projection_map = WebSearchProjectionMap()
    search_start = search_projection_map.project_start(
        "duckduckgo_results_json",
        raw_input={"query": "acpkit"},
    )
    assert search_start is not None
    assert search_start.title == "Search web for acpkit"
    assert search_start.content is not None
    search_start_content = search_start.content[0]
    assert isinstance(search_start_content, ContentToolCallContent)
    assert search_start_content.content.text == "Query: acpkit"

    search_progress = search_projection_map.project_progress(
        "google_serper_results_json",
        raw_input={"query": "acpkit"},
        raw_output=(
            "{'organic': [{'title': 'ACP Kit', 'link': 'https://example.com/acpkit', "
            "'snippet': 'Truthful ACP adapters.'}]}"
        ),
        serialized_output="fallback output",
        status="completed",
    )
    assert search_progress is not None
    assert search_progress.title == "Search web for acpkit"
    assert search_progress.content is not None
    search_progress_content = search_progress.content[0]
    assert isinstance(search_progress_content, ContentToolCallContent)
    assert "1. ACP Kit" in search_progress_content.content.text
    assert "https://example.com/acpkit" in search_progress_content.content.text

    search_fallback = search_projection_map.project_progress(
        "jina_search",
        raw_input={"query": "acpkit"},
        raw_output={"unexpected": "shape"},
        serialized_output="fallback output",
        status="completed",
    )
    assert search_fallback is not None
    assert search_fallback.content is not None
    search_fallback_content = search_fallback.content[0]
    assert isinstance(search_fallback_content, ContentToolCallContent)
    assert search_fallback_content.content.text == "fallback output"

    fetch_projection_map = HttpRequestProjectionMap()
    fetch_start = fetch_projection_map.project_start(
        "requests_get",
        raw_input='{"url": "https://example.com/docs"}',
    )
    assert fetch_start is not None
    assert fetch_start.title == "GET https://example.com/docs"
    assert fetch_start.content is not None
    fetch_start_content = fetch_start.content[0]
    assert isinstance(fetch_start_content, ContentToolCallContent)
    assert fetch_start_content.content.text == "URL: https://example.com/docs"

    fetch_progress = fetch_projection_map.project_progress(
        "requests_get",
        raw_input="https://example.com/docs",
        raw_output=(
            '{"url": "https://example.com/docs", "title": "Example Docs", '
            '"content": "Fetched page body"}'
        ),
        serialized_output="Fetched page body",
        status="completed",
    )
    assert fetch_progress is not None
    assert fetch_progress.title == "GET https://example.com/docs"
    assert fetch_progress.content is not None
    fetch_progress_content = fetch_progress.content[0]
    assert isinstance(fetch_progress_content, ContentToolCallContent)
    assert "Title: Example Docs" in fetch_progress_content.content.text
    assert "Fetched page body" in fetch_progress_content.content.text

    assert (
        fetch_projection_map.project_progress(
            "requests_get",
            raw_input={"url": "https://example.com/docs"},
            raw_output={"ignored": True},
            serialized_output="fallback fetch",
            status="in_progress",
        )
        is None
    )
    post_start = fetch_projection_map.project_start(
        "requests_post",
        raw_input='{"url": "https://example.com/api", "data": {"ok": true}}',
    )
    assert post_start is not None
    assert post_start.title == "POST https://example.com/api"

    delete_progress = fetch_projection_map.project_progress(
        "requests_delete",
        raw_input="https://example.com/api/item/1",
        raw_output="Deleted.",
        serialized_output="Deleted.",
        status="completed",
    )
    assert delete_progress is not None
    assert delete_progress.title == "DELETE https://example.com/api/item/1"
    assert delete_progress.content is not None
    delete_content = delete_progress.content[0]
    assert isinstance(delete_content, ContentToolCallContent)
    assert delete_content.content.text == "Deleted."


def test_browser_and_command_projection_maps_render_truthful_status() -> None:
    browser_projection_map = BrowserProjectionMap()

    navigate_start = browser_projection_map.project_start(
        "navigate_browser",
        raw_input={"url": "https://example.com/docs"},
    )
    assert navigate_start is not None
    assert navigate_start.title == "Navigate https://example.com/docs"
    assert navigate_start.content is not None
    navigate_start_content = navigate_start.content[0]
    assert isinstance(navigate_start_content, ContentToolCallContent)
    assert navigate_start_content.content.text == "URL: https://example.com/docs"

    links_progress = browser_projection_map.project_progress(
        "extract_hyperlinks",
        raw_input={},
        raw_output='["https://example.com/a", "https://example.com/b"]',
        serialized_output='["https://example.com/a", "https://example.com/b"]',
        status="completed",
    )
    assert links_progress is not None
    assert links_progress.content is not None
    links_content = links_progress.content[0]
    assert isinstance(links_content, ContentToolCallContent)
    assert "1. https://example.com/a" in links_content.content.text

    elements_progress = browser_projection_map.project_progress(
        "get_elements",
        raw_input={"selector": "a"},
        raw_output='[{"text": "Docs", "selector": "a.docs"}]',
        serialized_output='[{"text": "Docs", "selector": "a.docs"}]',
        status="completed",
    )
    assert elements_progress is not None
    assert elements_progress.content is not None
    elements_content = elements_progress.content[0]
    assert isinstance(elements_content, ContentToolCallContent)
    assert elements_content.content.text == "1. Docs (a.docs)"

    click_start = browser_projection_map.project_start(
        "click_element",
        raw_input={"selector": "button.submit"},
    )
    assert click_start is not None
    assert click_start.title == "Click button.submit"

    click_progress = browser_projection_map.project_progress(
        "click_element",
        raw_input={"selector": "button.submit"},
        raw_output="Clicked element 'button.submit'",
        serialized_output="Clicked element 'button.submit'",
        status="completed",
    )
    assert click_progress is not None
    assert click_progress.content is not None
    click_content = click_progress.content[0]
    assert isinstance(click_content, ContentToolCallContent)
    assert click_content.content.text == "Clicked element 'button.submit'"

    command_projection_map = CommandProjectionMap()
    command_start = command_projection_map.project_start(
        "terminal",
        raw_input={"commands": ["pwd", "ls"]},
    )
    assert command_start is not None
    assert command_start.title == "pwd ls"
    assert command_start.content is not None
    command_start_content = command_start.content[0]
    assert isinstance(command_start_content, ContentToolCallContent)
    assert command_start_content.content.text == "pwd\nls"

    command_progress = command_projection_map.project_progress(
        "terminal",
        raw_input={"commands": ["pwd", "ls"]},
        raw_output="workspace\nREADME.md",
        serialized_output="workspace\nREADME.md",
        status="completed",
    )
    assert command_progress is not None
    assert command_progress.content is not None
    command_progress_content = command_progress.content[0]
    assert isinstance(command_progress_content, ContentToolCallContent)
    assert command_progress_content.content.text == "workspace\nREADME.md"


def test_community_file_management_projection_map_renders_file_operations() -> None:
    projection_map = CommunityFileManagementProjectionMap()

    file_search_start = projection_map.project_start(
        "file_search",
        raw_input={"dir_path": "src", "pattern": "*.py"},
    )
    assert file_search_start is not None
    assert file_search_start.title == "Search `*.py` in `src`"
    assert file_search_start.locations == [ToolCallLocation(path="src")]
    assert file_search_start.content is not None
    file_search_content = file_search_start.content[0]
    assert isinstance(file_search_content, ContentToolCallContent)
    assert file_search_content.content.text == "Directory: src\nPattern: *.py"

    move_start = projection_map.project_start(
        "move_file",
        raw_input={"source_path": "draft.txt", "destination_path": "archive/draft.txt"},
    )
    assert move_start is not None
    assert move_start.title == "Move `draft.txt` -> `archive/draft.txt`"
    assert move_start.locations == [
        ToolCallLocation(path="draft.txt"),
        ToolCallLocation(path="archive/draft.txt"),
    ]

    delete_progress = projection_map.project_progress(
        "file_delete",
        raw_input={"file_path": "draft.txt"},
        raw_output="File deleted successfully: draft.txt.",
        serialized_output="File deleted successfully: draft.txt.",
        status="completed",
    )
    assert delete_progress is not None
    assert delete_progress.title == "Delete `draft.txt`"
    assert delete_progress.content is not None
    delete_progress_content = delete_progress.content[0]
    assert isinstance(delete_progress_content, ContentToolCallContent)
    assert delete_progress_content.content.text == "File deleted successfully: draft.txt."


def test_finance_projection_map_renders_lookup_and_dataset_calls() -> None:
    projection_map = FinanceProjectionMap()

    search_start = projection_map.project_start(
        "google_finance",
        raw_input={"query": "NVDA"},
    )
    assert search_start is not None
    assert search_start.title == "Search finance for NVDA"
    assert search_start.content is not None
    search_start_content = search_start.content[0]
    assert isinstance(search_start_content, ContentToolCallContent)
    assert search_start_content.content.text == "Query: NVDA"

    search_progress = projection_map.project_progress(
        "yahoo_finance_news",
        raw_input={"query": "AAPL"},
        raw_output="Apple jumps after earnings.",
        serialized_output="Apple jumps after earnings.",
        status="completed",
    )
    assert search_progress is not None
    assert search_progress.title == "Search finance for AAPL"
    assert search_progress.content is not None
    search_progress_content = search_progress.content[0]
    assert isinstance(search_progress_content, ContentToolCallContent)
    assert search_progress_content.content.text == "Apple jumps after earnings."

    dataset_progress = projection_map.project_progress(
        "income_statements",
        raw_input={"ticker": "MSFT", "period": "annual"},
        raw_output='[{"revenue": 10}]',
        serialized_output='[{"revenue": 10}]',
        status="completed",
    )
    assert dataset_progress is not None
    assert dataset_progress.title == "Get income statements for MSFT (annual)"
    assert dataset_progress.content is not None
    dataset_progress_content = dataset_progress.content[0]
    assert isinstance(dataset_progress_content, ContentToolCallContent)
    assert dataset_progress_content.content.text == '[{"revenue": 10}]'


def test_projection_maps_cover_negative_and_default_paths() -> None:
    classifier = DefaultToolClassifier()
    assert classifier.classify("file_search") == "search"
    assert classifier.classify("income_statements") == "read"
    assert classifier.classify("fetch_page") == "fetch"
    assert classifier.classify("update_note") == "edit"

    search_projection_map = WebSearchProjectionMap()
    assert search_projection_map.project_start("unknown", raw_input={"query": "acp"}) is None
    assert search_projection_map.project_start("duckduckgo_search", raw_input={}) is None
    assert (
        search_projection_map.project_progress(
            "duckduckgo_search",
            raw_input={"query": "acp"},
            raw_output=[],
            serialized_output="pending",
            status="in_progress",
        )
        is None
    )

    fetch_projection_map = HttpRequestProjectionMap()
    assert (
        fetch_projection_map.project_start("unknown", raw_input={"url": "https://example.com"})
        is None
    )
    assert fetch_projection_map.project_start("requests_get", raw_input={}) is None
    assert (
        fetch_projection_map.project_progress(
            "unknown",
            raw_input={"url": "https://example.com"},
            raw_output="ok",
            serialized_output="ok",
            status="completed",
        )
        is None
    )

    browser_projection_map = BrowserProjectionMap()
    assert browser_projection_map.project_start("navigate_browser", raw_input={}) is None
    assert browser_projection_map.project_start("click_element", raw_input={}) is None
    get_elements_start = browser_projection_map.project_start("get_elements", raw_input={})
    assert get_elements_start is not None
    assert get_elements_start.title == "Inspect page elements"
    previous_start = browser_projection_map.project_start("previous_webpage", raw_input={})
    assert previous_start is not None
    assert previous_start.title == "Navigate back"
    previous_progress = browser_projection_map.project_progress(
        "previous_webpage",
        raw_input={},
        raw_output="Went back",
        serialized_output="Went back",
        status="completed",
    )
    assert previous_progress is not None
    assert previous_progress.title == "Navigate back"
    assert (
        browser_projection_map.project_progress(
            "extract_text",
            raw_input={},
            raw_output="text",
            serialized_output="text",
            status="in_progress",
        )
        is None
    )

    command_projection_map = CommandProjectionMap()
    fallback_progress = command_projection_map.project_progress(
        "terminal",
        raw_input={},
        raw_output={},
        serialized_output="fallback terminal output",
        status="completed",
    )
    assert fallback_progress is not None
    assert fallback_progress.title == "Run shell command"
    assert fallback_progress.content is not None
    no_output_progress = command_projection_map.project_progress(
        "terminal",
        raw_input={},
        raw_output={},
        serialized_output="",
        status="completed",
    )
    assert no_output_progress is not None
    assert no_output_progress.title == "Run shell command"
    assert no_output_progress.content is None

    file_projection_map = CommunityFileManagementProjectionMap()
    passthrough_read_start = file_projection_map.project_start(
        "read_file",
        raw_input={"file_path": "notes.txt"},
    )
    assert passthrough_read_start is not None
    assert passthrough_read_start.title == "Read `notes.txt`"
    list_start = file_projection_map.project_start("list_directory", raw_input={})
    assert list_start is not None
    assert list_start.title == "List `.`"
    file_search_start = file_projection_map.project_start("file_search", raw_input={})
    assert file_search_start is not None
    assert file_search_start.title == "Search files in `.`"
    assert file_search_start.content is not None
    copy_start = file_projection_map.project_start(
        "copy_file",
        raw_input={"source_path": "a.txt", "destination_path": "b.txt"},
    )
    assert copy_start is not None
    assert copy_start.title == "Copy `a.txt` -> `b.txt`"
    assert file_projection_map.project_start("copy_file", raw_input={"source_path": "a"}) is None
    delete_start = file_projection_map.project_start(
        "file_delete", raw_input={"file_path": "a.txt"}
    )
    assert delete_start is not None
    assert delete_start.title == "Delete `a.txt`"
    assert file_projection_map.project_start("file_delete", raw_input={}) is None
    assert (
        file_projection_map.project_progress(
            "list_directory",
            raw_input={},
            raw_output=["a.txt"],
            serialized_output="a.txt",
            status="in_progress",
        )
        is None
    )
    passthrough_read_progress = file_projection_map.project_progress(
        "read_file",
        raw_input={"file_path": "notes.txt"},
        raw_output="hello",
        serialized_output="hello",
        status="completed",
    )
    assert passthrough_read_progress is not None
    assert passthrough_read_progress.title == "Read `notes.txt`"
    file_search_progress = file_projection_map.project_progress(
        "file_search",
        raw_input={},
        raw_output=[],
        serialized_output="notes.txt",
        status="completed",
    )
    assert file_search_progress is not None
    assert file_search_progress.title == "Search files in `.`"
    list_progress = file_projection_map.project_progress(
        "list_directory",
        raw_input={},
        raw_output=["a.txt"],
        serialized_output="a.txt",
        status="completed",
    )
    assert list_progress is not None
    assert list_progress.title == "List `.`"
    copy_progress = file_projection_map.project_progress(
        "copy_file",
        raw_input={"source_path": "a.txt", "destination_path": "b.txt"},
        raw_output="File copied successfully from a.txt to b.txt.",
        serialized_output="File copied successfully from a.txt to b.txt.",
        status="completed",
    )
    assert copy_progress is not None
    assert copy_progress.title == "Copy `a.txt` -> `b.txt`"
    mutation_none = file_projection_map.project_progress(
        "unknown",
        raw_input={},
        raw_output={},
        serialized_output="",
        status="completed",
    )
    assert mutation_none is None
    assert file_projection_map.project_start("unknown", raw_input={}) is None

    finance_projection_map = FinanceProjectionMap()
    search_without_query = finance_projection_map.project_start("google_finance", raw_input={})
    assert search_without_query is not None
    assert search_without_query.title == "Search finance"
    dataset_start = finance_projection_map.project_start(
        "balance_sheets",
        raw_input={"ticker": "NVDA", "period": "quarterly"},
    )
    assert dataset_start is not None
    assert dataset_start.title == "Get balance sheets for NVDA (quarterly)"
    assert (
        finance_projection_map.project_progress(
            "google_finance",
            raw_input={"query": "NVDA"},
            raw_output="done",
            serialized_output="done",
            status="in_progress",
        )
        is None
    )
    assert finance_projection_map.project_start("unknown", raw_input={}) is None
    assert (
        finance_projection_map.project_progress(
            "unknown",
            raw_input={},
            raw_output="done",
            serialized_output="done",
            status="completed",
        )
        is None
    )


def test_projection_private_helper_commands_cover_remaining_paths() -> None:
    assert _truncate_text("abcd", limit=3) == "ab…"
    assert _http_method_label("custom_fetch") == "Fetch"
    assert _command_text({"commands": "pwd"}) == "pwd"
    assert _command_text({"commands": ["", "pwd", "ls"]}) == "pwd\nls"
    assert _command_text({"commands": []}) is None
    assert _parse_structured_value("") is None
    assert _parse_structured_value('{"query": "acp"}') == {"query": "acp"}
    assert _parse_structured_value("{'query': 'acp'}") == {"query": "acp"}
    assert _parse_structured_value("not-json") is None

    assert _web_search_query('{"query": "acp"}') == "acp"
    assert _web_search_query("acp") == "acp"
    assert _web_search_query(12) is None
    assert _web_fetch_url('{"url": "https://example.com"}') == "https://example.com"
    assert _web_fetch_url("not-a-url") is None
    assert _web_fetch_url(12) is None
    assert (
        _web_fetch_url({"payload": '{"href": "https://example.com/x"}'}) == "https://example.com/x"
    )
    assert _web_fetch_url({"payload": '{"href": 1}'}) is None
    assert _web_fetch_url({"payload": "[1, 2]"}) is None
    assert _format_web_search_start({}) == "Searching the web."
    assert _format_web_fetch_start({}) == "Fetching web content."

    assert (
        _format_web_search_progress(
            [{"title": "Result 1"}],
            "fallback",
        )
        == "1. Result 1"
    )
    assert _format_web_search_progress({"bad": True}, "fallback") == "fallback"
    assert _search_result_rows([]) is None
    assert _search_result_rows("bad") is None
    assert _search_result_rows({"items": [{"title": "ACP"}]}) == [{"title": "ACP"}]
    assert _search_result_rows({"title": "ACP"}) == [{"title": "ACP"}]
    assert _search_result_rows({"items": [1, 2]}) is None
    assert _normalize_search_result_row({}, index=1) is None
    normalized = _normalize_search_result_row({"url": "https://example.com"}, index=2)
    assert normalized is not None
    assert normalized.title == "Result 2"
    assert _normalized_search_results('[{"title": "ACP"}]') is not None
    mixed_normalized = _normalized_search_results('[{}, {"title": "ACP"}]')
    assert mixed_normalized is not None
    assert mixed_normalized[0].title == "ACP"

    assert _format_web_fetch_progress("raw body", "raw body") == "raw body"
    assert _format_web_fetch_progress({"ignored": True}, "fallback fetch") == "fallback fetch"
    title_only = _format_web_fetch_progress({"title": "Example"}, "fallback")
    assert title_only == "Title: Example"
    content_only = _format_web_fetch_progress({"content": "Hello world"}, "fallback")
    assert "Preview:" in content_only

    assert _browser_read_title("current_webpage") == "Read current page"
    assert _browser_read_title("extract_text") == "Extract page text"
    assert _browser_read_title("get_elements") == "Inspect page elements"
    assert _browser_read_title("other") == "other"
    assert _browser_action_title("previous_webpage") == "Navigate back"
    assert _browser_action_title("other") == "other"
    assert _browser_progress_title("extract_text") == "Extract page text"
    assert _browser_progress_title("previous_webpage") == "Navigate back"
    assert _browser_progress_title("other") == "other"
    assert _browser_text_preview({"structured": True}, "serialized") == "serialized"
    assert _file_management_locations("bad") is None
    assert _file_management_locations({}) is None
    assert _file_management_mutation_title("copy_file", raw_input="bad") is None
    assert (
        _file_management_mutation_title(
            "copy_file",
            raw_input={"source_path": "a.txt", "destination_path": "b.txt"},
        )
        == "Copy `a.txt` -> `b.txt`"
    )
    assert _file_management_mutation_title("copy_file", raw_input={"source_path": "a.txt"}) is None
    assert (
        _file_management_mutation_title(
            "move_file",
            raw_input={"source_path": "a.txt", "destination_path": "b.txt"},
        )
        == "Move `a.txt` -> `b.txt`"
    )
    assert _file_management_mutation_title("move_file", raw_input={"source_path": "a.txt"}) is None
    assert (
        _file_management_mutation_title(
            "file_delete",
            raw_input={"file_path": "a.txt"},
        )
        == "Delete `a.txt`"
    )
    assert _file_management_mutation_title("file_delete", raw_input={}) is None
    assert _finance_query('{"ticker": "AAPL"}') == "AAPL"
    assert _finance_query("AAPL") == "AAPL"
    assert _finance_query(12) is None
    assert _finance_dataset_title("income_statements", raw_input={}) == "Get income statements"
    assert (
        _finance_dataset_title("income_statements", raw_input={"ticker": "AAPL"})
        == "Get income statements for AAPL"
    )
    assert (
        _finance_dataset_title(
            "balance_sheets",
            raw_input={"ticker": "AAPL", "period": "annual"},
        )
        == "Get balance sheets for AAPL (annual)"
    )
    assert (
        _finance_dataset_title("income_statements", raw_input="AAPL")
        == "Get income statements for AAPL"
    )
    assert _format_browser_link_results("not-list", "fallback") == "fallback"
    assert (
        _format_browser_link_results('["", "https://example.com"]', "fallback")
        == "2. https://example.com"
    )
    assert _format_browser_link_results("[]", "fallback") == "fallback"
    assert _format_browser_link_results("[1, 2]", "fallback") == "fallback"
    assert _format_browser_element_results("not-list", "fallback") == "fallback"
    assert _format_browser_element_results([{"text": "Docs"}], "fallback") == "1. Docs"
    assert _format_browser_element_results('[1, {"text": "Docs"}]', "fallback") == "2. Docs"
    assert _format_browser_element_results('[{"selector": "a.docs"}]', "fallback") == "1. a.docs"
    assert _format_browser_element_results("[1, 2]", "fallback") == "fallback"
    assert _format_browser_element_results("[{}]", "fallback") == "fallback"


def test_default_output_serializer_handles_supported_shapes() -> None:
    serializer = DefaultOutputSerializer()

    assert serializer.serialize("plain") == "plain"
    assert serializer.serialize(b"hello") == "hello"
    assert '"enabled": true' in serializer.serialize(_ModelPayload(name="demo", enabled=True))
    assert '"size": 2' in serializer.serialize(_DataclassPayload(name="demo", size=2))
    assert serializer.serialize({"data": [1, 2, 3]}).startswith("{\n")
    assert serializer.serialize((1, True, None)).startswith("[\n")
    assert serializer.serialize(object()).startswith("<object object")
    assert _json_compatible(object()).startswith("<object object")

    compatible = _json_compatible(
        {
            "bytes": b"hi",
            "model": _ModelPayload(name="demo", enabled=True),
            "dataclass": _DataclassPayload(name="demo", size=2),
            "tuple": (1, 2),
        }
    )
    assert compatible == {
        "bytes": "hi",
        "model": {"enabled": True, "name": "demo"},
        "dataclass": {"name": "demo", "size": 2},
        "tuple": [1, 2],
    }


def test_structured_event_projection_map_normalizes_explicit_event_payloads() -> None:
    projection_map = StructuredEventProjectionMap()

    projected = projection_map.project_event_payload(
        {
            "events": [
                {
                    "type": "tool_call",
                    "toolCallId": "tool-1",
                    "title": "read_file",
                    "kind": "read",
                    "status": "in_progress",
                },
                {
                    "session_update": "tool_call_update",
                    "toolCallId": "tool-1",
                    "status": "completed",
                    "content": "done",
                },
                {
                    "type": "agent_message_chunk",
                    "content": "Projected text",
                    "messageId": "m-1",
                },
            ]
        }
    )

    assert projected is not None
    assert isinstance(projected[0], ToolCallStart)
    assert isinstance(projected[1], ToolCallProgress)
    assert isinstance(projected[2], AgentMessageChunk)
    assert projected[1].content is not None
    assert cast(Any, projected[1].content[0]).type == "content"
    assert projected[2].content.text == "Projected text"

    assert projection_map.project_event_payload({"events": ["bad"]}) is None
    assert projection_map.project_event_payload({"payload": "ignored"}) is None

    composite = compose_event_projection_maps((projection_map, projection_map))
    assert composite is not None
    double_projected = composite.project_event_payload(
        {
            "events": [
                {
                    "type": "tool_call",
                    "toolCallId": "tool-2",
                    "title": "echo hi",
                    "kind": "execute",
                    "status": "in_progress",
                }
            ]
        }
    )
    assert double_projected is not None
    assert len(double_projected) == 2


def test_structured_event_projection_map_covers_direct_list_and_invalid_variants() -> None:
    projection_map = StructuredEventProjectionMap()

    direct_list = projection_map.project_event_payload(
        [
            {
                "sessionUpdate": "session_info_update",
                "title": "Projected session",
                "updatedAt": utc_now().isoformat(),
            },
            {
                "type": "plan",
                "entries": [
                    {
                        "content": "Inspect repo",
                        "status": "pending",
                        "priority": "high",
                    }
                ],
            },
            {
                "type": "user_message_chunk",
                "content": "hello",
                "messageId": "u-1",
            },
        ]
    )

    assert direct_list is not None
    assert isinstance(direct_list[0], SessionInfoUpdate)
    assert isinstance(direct_list[1], AgentPlanUpdate)
    assert isinstance(direct_list[2], UserMessageChunk)
    assert compose_event_projection_maps(None) is None
    assert compose_event_projection_maps(()) is None
    assert compose_event_projection_maps((projection_map,)) is projection_map

    assert (
        projection_map.project_event_payload(
            {
                "events": [
                    {"type": "tool_call", "toolCallId": 1},
                    {
                        "type": "tool_call_update",
                        "content": {"type": "text", "text": 1},
                    },
                    {
                        "type": "agent_message_chunk",
                        "content": {"type": "text", "text": 1},
                    },
                ]
            }
        )
        is None
    )


def test_prompt_conversion_covers_audio_blob_and_defensive_embedded_resource_paths() -> None:
    audio_content = prompt_to_langchain_content(
        [
            EmbeddedResourceContentBlock(
                type="resource",
                resource=BlobResourceContents(
                    uri="file:///audio.wav",
                    blob="d2F2",
                    mime_type="audio/wav",
                ),
            ),
            ResourceContentBlock(
                type="resource_link",
                name="doc",
                title="Doc",
                uri="file:///doc",
                mime_type="text/markdown",
                size=42,
            ),
        ]
    )
    assert audio_content[0] == {
        "type": "audio",
        "base64": "d2F2",
        "mime_type": "audio/wav",
    }
    assert "MIME: text/markdown" in audio_content[1]["text"]
    assert "Size: 42 bytes" in audio_content[1]["text"]

    unknown_resource = _embedded_resource_content(
        cast(
            EmbeddedResourceContentBlock,
            SimpleNamespace(resource=SimpleNamespace(uri="file:///opaque")),
        )
    )
    assert unknown_resource == [{"type": "text", "text": "Embedded resource: file:///opaque"}]

    no_mime_content = prompt_to_langchain_content(
        [
            EmbeddedResourceContentBlock(
                type="resource",
                resource=TextResourceContents(uri="file:///plain.txt", text="plain"),
            ),
            EmbeddedResourceContentBlock(
                type="resource",
                resource=BlobResourceContents(uri="file:///blob.bin", blob="AA=="),
            ),
        ]
    )
    assert "MIME:" not in no_mime_content[0]["text"]
    assert "MIME:" not in no_mime_content[1]["text"]


def test_event_projection_private_helpers_cover_invalid_paths() -> None:
    assert _extract_event_payloads("bad", event_keys=frozenset({"events"})) is None
    assert _extract_event_payloads({"sessionUpdate": "tool_call"}, event_keys=frozenset()) == (
        {"sessionUpdate": "tool_call"},
    )
    assert _resolve_session_update_kind({"sessionUpdate": "bad"}) is None
    assert _event_payload_to_update({"payload": "ignored"}) is None
    assert _event_payload_to_update({"type": 1}) is None
    assert _normalize_text_content({"type": "text", "text": 1}) is None
    assert _normalize_text_content(None) is None
    assert (
        _event_payload_to_update({"type": "agent_message_chunk", "content": {"type": "text"}})
        is None
    )
    assert (
        _event_payload_to_update({"type": "user_message_chunk", "content": {"type": "text"}})
        is None
    )
    assert _event_payload_to_update({"type": "tool_call", "toolCallId": 1}) is None
    assert (
        _event_payload_to_update({"type": "tool_call_update", "content": {"bad": "shape"}}) is None
    )
    assert _event_payload_to_update({"type": "session_info_update", "updatedAt": 1}) is None
    assert _event_payload_to_update({"type": "plan", "entries": "bad"}) is None

    @dataclass(slots=True, frozen=True, kw_only=True)
    class _NullEventMap:
        def project_event_payload(
            self,
            payload: Any,
        ) -> tuple[AgentMessageChunk, ...] | None:
            del payload
            return None

    composite = compose_event_projection_maps(
        (
            StructuredEventProjectionMap(),
            _NullEventMap(),
        )
    )
    assert composite is not None
    assert (
        composite.project_event_payload([1, {"type": "agent_message_chunk", "content": "ok"}])
        is not None
    )


def test_projection_private_helpers_cover_remaining_paths() -> None:
    assert _format_command_title("") == "command"
    assert _command_risk_note("echo safe") is None
    assert _command_title_from_input("bad") is None
    assert _command_title_from_input({"path": "notes.txt"}) is None
    assert _tool_title("other_tool", path=None) is None
    assert _tool_title("other_tool", path="notes.txt") is None
    assert _search_title("grep", search_term="needle", path=None) == "Search `needle`"
    assert _search_title("ls", search_term=None, path=None) == "List files"
    assert _search_title("other", search_term=None, path="src") == "other"
    assert _output_text({"result": "done"}, "fallback") == "done"
    assert _output_text("bad", "fallback") == "fallback"
    assert _terminal_id({"terminalId": "term-2"}) == "term-2"
    assert _terminal_id("bad") is None
    assert _first_string({"path": "notes.txt"}, ("path", "other")) == "notes.txt"
    assert _first_string({"path": 1}, ("path",)) is None


def test_stored_session_update_round_trips_all_supported_update_kinds() -> None:
    updates = [
        AgentMessageChunk(
            session_update="agent_message_chunk",
            content=TextContentBlock(type="text", text="agent"),
            message_id="m1",
        ),
        AgentPlanUpdate(
            session_update="plan",
            entries=[PlanEntry(content="Inspect repo", priority="high", status="pending")],
        ),
        SessionInfoUpdate(
            session_update="session_info_update",
            title="Demo title",
            updated_at=utc_now().isoformat(),
        ),
        ToolCallStart(
            session_update="tool_call",
            tool_call_id="tool-1",
            title="read_file",
            kind="read",
            status="in_progress",
        ),
        ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id="tool-1",
            status="completed",
        ),
        UserMessageChunk(
            session_update="user_message_chunk",
            content=TextContentBlock(type="text", text="user"),
            message_id="u1",
        ),
    ]

    for update in updates:
        stored = StoredSessionUpdate.from_update(cast(Any, update))
        restored = stored.to_update()
        assert restored.model_dump(
            mode="json", by_alias=True, exclude_none=True
        ) == update.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )

    with pytest.raises(TypeError):
        StoredSessionUpdate.from_update(cast(Any, TextContentBlock(type="text", text="bad")))

    now = utc_now()
    assert now.tzinfo is not None

    assert _coerce_json_value({"ok": [1, True, None]}) == {"ok": [1, True, None]}

    with pytest.raises(TypeError):
        _coerce_json_object("bad")
    with pytest.raises(TypeError):
        _coerce_json_object({1: "bad"})
    with pytest.raises(TypeError):
        _coerce_json_value(object())
    invalid_update = StoredSessionUpdate(kind="plan", payload={})
    cast(Any, invalid_update).kind = "bad-kind"
    with pytest.raises(AssertionError):
        invalid_update.to_update()


def test_memory_and_file_session_stores_cover_lifecycle(tmp_path: Path) -> None:
    store = MemorySessionStore()
    session = _make_session(cwd=tmp_path / "workspace")
    session.title = "Demo session"
    session.config_values["phase"] = True
    session.plan_markdown = "# Stored plan"
    session.plan_entries = [
        {
            "content": "Inspect repo",
            "status": "pending",
            "priority": "high",
        }
    ]
    session.transcript.append(
        StoredSessionUpdate.from_update(
            UserMessageChunk(
                session_update="user_message_chunk",
                content=TextContentBlock(type="text", text="hello"),
                message_id="u1",
            )
        )
    )

    store.save(session)
    loaded = store.get(session.session_id)
    assert loaded is not None
    loaded.title = "Mutated"
    stored_again = store.get(session.session_id)
    assert stored_again is not None
    assert stored_again.title == "Demo session"

    forked = store.fork(session.session_id, new_session_id="fork-1", cwd=tmp_path / "forked")
    assert forked is not None
    assert forked.session_id == "fork-1"
    assert forked.cwd == tmp_path / "forked"
    assert store.fork("missing", new_session_id="fork-2", cwd=tmp_path / "missing") is None
    assert [item.session_id for item in store.list_sessions()] == [
        "fork-1",
        "session-1",
    ]
    store.delete(session.session_id)
    assert store.get(session.session_id) is None

    stale_path = tmp_path / ".acpkit-session-stale.tmp"
    stale_path.write_text("stale", encoding="utf-8")
    file_store = FileSessionStore(tmp_path)
    assert not stale_path.exists()

    file_store.save(session)
    file_loaded = file_store.get(session.session_id)
    assert file_loaded is not None
    assert file_loaded.transcript[0].to_update().message_id == "u1"
    assert file_loaded.plan_markdown == "# Stored plan"
    assert file_loaded.plan_entries == session.plan_entries

    session.updated_at = utc_now()
    file_store.save(session)
    session_copy = file_store.fork(
        session.session_id, new_session_id="fork-file", cwd=tmp_path / "fork"
    )
    assert session_copy is not None
    assert session_copy.cwd == tmp_path / "fork"
    assert file_store.fork("missing", new_session_id="missing-fork", cwd=tmp_path / "fork") is None
    assert {item.session_id for item in file_store.list_sessions()} == {
        "session-1",
        "fork-file",
    }

    broken_path = tmp_path / "broken.json"
    broken_path.write_text("{bad json", encoding="utf-8")
    assert file_store.get("broken") is None
    broken_path.write_text('{"session_id":"broken"}', encoding="utf-8")
    assert file_store.get("broken") is None

    file_store.delete("fork-file")
    assert file_store.get("fork-file") is None
    file_store.delete("already-missing")

    lock_one = _store_lock(tmp_path)
    lock_two = _store_lock(tmp_path)
    assert lock_one is lock_two

    file_store._fsync_directory()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "langchain_acp.session.store.os.open",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("open failed")),
        )
        file_store._fsync_directory()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "langchain_acp.session.store.os.fsync",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("fsync failed")),
        )
        file_store._fsync_directory()

    invalid_session_path = tmp_path / "invalid.json"
    invalid_session_path.write_text("[]", encoding="utf-8")
    assert all(item.session_id != "invalid" for item in file_store.list_sessions())


def test_native_plan_runtime_and_tools_cover_phase4_paths(tmp_path: Path) -> None:
    persistence_provider = _PlanPersistenceProvider()
    adapter = _make_adapter(
        config=AdapterConfig(
            available_modes=[
                SessionMode(id="ask", name="Ask"),
                SessionMode(id="plan", name="Plan"),
            ],
            default_mode_id="plan",
            default_plan_generation_type="tools",
            enable_plan_progress_tools=True,
            native_plan_additional_instructions="Stay concise.",
            native_plan_persistence_provider=persistence_provider,
            plan_mode_id="plan",
        )
    )
    client = RecordingACPClient()
    adapter.on_connect(cast(AcpClient, client))
    created = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    assert created.config_options is not None
    assert [option.id for option in created.config_options] == [
        "mode",
        "plan_generation_type",
    ]
    session = adapter._require_session(created.session_id)
    runtime = _NativePlanRuntime(adapter)

    assert runtime.supports_native_plan_state(session) is True
    assert runtime.supports_native_plan_progress(session) is True
    assert runtime.supports_native_plan_writes(session) is True
    assert runtime.requires_structured_plan_output(session) is False
    assert runtime.current_plan_generation_type(session) == "tools"
    config_options = asyncio.run(runtime.config_options(session))
    assert [option.id for option in config_options] == ["plan_generation_type"]

    with _bind_native_plan_context(runtime, session):
        assert acp_get_plan() == "No plan has been recorded yet."
        assert (
            asyncio.run(
                acp_set_plan(
                    [
                        PlanEntry(
                            content="Inspect repo",
                            status="pending",
                            priority="high",
                        )
                    ],
                    "# Native plan",
                )
            )
            == "Recorded 1 plan entries."
        )
        updated = asyncio.run(acp_update_plan_entry(1, status="in_progress"))
        assert updated.startswith("Updated plan entry 1:")
        done = asyncio.run(acp_mark_plan_done(1))
        assert done == "Marked plan entry 1 as completed: Inspect repo"
        formatted = acp_get_plan()

    assert "Current plan entries:" in formatted
    assert "Additional plan instructions:" in formatted
    assert persistence_provider.calls[-1][1] == "# Native plan"
    assert session.plan_entries == [
        {
            "content": "Inspect repo",
            "status": "completed",
            "priority": "high",
        }
    ]
    plan_updates = [
        cast(AgentPlanUpdate, update)
        for _, update in client.updates
        if isinstance(update, AgentPlanUpdate)
    ]
    assert len(plan_updates) == 3

    with pytest.raises(RequestError):
        asyncio.run(runtime.update_native_plan_entry(session, index=0))


def test_native_plan_runtime_structured_mode_and_unbound_tools() -> None:
    adapter = _make_adapter(
        config=AdapterConfig(
            available_modes=[SessionMode(id="plan", name="Plan")],
            default_mode_id="plan",
            default_plan_generation_type="structured",
            plan_mode_id="plan",
        )
    )
    session = _make_session()
    session.session_mode_id = "plan"
    runtime = _NativePlanRuntime(adapter)

    assert runtime.requires_structured_plan_output(session) is True
    assert runtime.uses_structured_plan_generation(session) is True
    assert runtime.supports_native_plan_writes(session) is False
    session.config_values["plan_generation_type"] = "bad"
    assert runtime.current_plan_generation_type(session) == "structured"
    assert acp_get_plan() == "No active ACP session is bound."
    assert asyncio.run(acp_set_plan([])) == "No active ACP session is bound."
    assert asyncio.run(acp_update_plan_entry(1)) == "No active ACP session is bound."
    assert asyncio.run(acp_mark_plan_done(1)) == "No active ACP session is bound."


def test_task_plan_validation_and_payload_parsing() -> None:
    parsed = TaskPlan.model_validate(
        {
            "plan_md": "# Plan",
            "plan_entries": [
                {
                    "content": "Inspect repo",
                    "status": "pending",
                    "priority": "high",
                }
            ],
        }
    )
    assert parsed.plan_entries[0].content == "Inspect repo"


def test_phase4_native_plan_runtime_negative_and_formatting_paths(
    tmp_path: Path,
) -> None:
    adapter = _make_adapter(config=AdapterConfig())
    runtime = _NativePlanRuntime(adapter)
    session = _make_session(cwd=tmp_path)

    assert runtime.supports_plan_generation_selection() is False
    assert asyncio.run(runtime.config_options(session)) == []
    assert runtime.supports_native_plan_state(session) is False
    assert runtime.supports_native_plan_progress(session) is False
    assert runtime.supports_native_plan_writes(session) is False
    assert runtime.requires_structured_plan_output(session) is False
    assert runtime.get_native_plan_entries(session) is None
    assert runtime.format_native_plan(session) == "No plan has been recorded yet."
    asyncio.run(runtime.emit_native_plan_update(session))
    asyncio.run(runtime.persist_current_native_plan_state(session))

    markdown_only_adapter = _make_adapter(
        config=AdapterConfig(
            available_modes=[SessionMode(id="plan", name="Plan")],
            default_mode_id="plan",
            default_plan_generation_type="structured",
            native_plan_additional_instructions="   ",
            plan_mode_id="plan",
            plan_provider=cast(Any, object()),
        )
    )
    markdown_runtime = _NativePlanRuntime(markdown_only_adapter)
    markdown_session = _make_session(cwd=tmp_path)
    markdown_session.session_mode_id = "plan"
    markdown_session.plan_markdown = "# Existing plan"
    assert markdown_runtime.supports_native_plan_state(markdown_session) is False
    assert markdown_runtime.supports_native_plan_writes(markdown_session) is False
    assert markdown_runtime.get_native_plan_entries(markdown_session) is None
    assert markdown_runtime.format_native_plan(markdown_session) == "# Existing plan"
    with pytest.raises(RequestError):
        asyncio.run(markdown_runtime.update_native_plan_entry(markdown_session, index=1))

    markdown_guidance_adapter = _make_adapter(
        config=AdapterConfig(
            available_modes=[SessionMode(id="plan", name="Plan")],
            default_mode_id="plan",
            default_plan_generation_type="structured",
            native_plan_additional_instructions="Keep milestones coarse.",
            plan_mode_id="plan",
        )
    )
    markdown_guidance_runtime = _NativePlanRuntime(markdown_guidance_adapter)
    markdown_guidance_session = _make_session(cwd=tmp_path)
    markdown_guidance_session.session_mode_id = "plan"
    markdown_guidance_session.plan_markdown = "# Existing plan"
    formatted_markdown = markdown_guidance_runtime.format_native_plan(markdown_guidance_session)
    assert "Additional plan instructions:" in formatted_markdown
    assert "Keep milestones coarse." in formatted_markdown

    entries_adapter = _make_adapter(
        config=AdapterConfig(
            available_modes=[SessionMode(id="plan", name="Plan")],
            default_mode_id="plan",
            default_plan_generation_type="tools",
            plan_mode_id="plan",
        )
    )
    entries_runtime = _NativePlanRuntime(entries_adapter)
    entries_session = _make_session(cwd=tmp_path)
    entries_session.session_mode_id = "plan"
    entries_session.plan_entries = [
        {
            "content": "Inspect repo",
            "status": "pending",
            "priority": "medium",
        }
    ]
    updated = asyncio.run(
        entries_runtime.update_native_plan_entry(
            entries_session,
            index=1,
            content="Inspect repository",
            priority="low",
        )
    )
    assert updated.content == "Inspect repository"
    assert updated.priority == "low"
    formatted = entries_runtime.format_native_plan(entries_session)
    assert "Use these 1-based entry numbers" in formatted
    assert "Inspect repository" in formatted
    entries_runtime_adapter_session = _make_session(cwd=tmp_path, session_id="persist-current")
    entries_runtime_adapter_session.session_mode_id = "plan"
    entries_runtime_adapter_session.plan_entries = [
        {"content": "Persist me", "status": "pending", "priority": "high"}
    ]
    asyncio.run(entries_runtime.persist_current_native_plan_state(entries_runtime_adapter_session))
    assert entries_runtime_adapter_session.plan_entries == [
        {"content": "Persist me", "status": "pending", "priority": "high"}
    ]


def test_phase4_adapter_helper_branches_cover_native_plan_edges(tmp_path: Path) -> None:
    adapter = _make_adapter(
        config=AdapterConfig(
            available_modes=[SessionMode(id="plan", name="Plan")],
            default_mode_id="plan",
            default_plan_generation_type="tools",
            plan_mode_id="plan",
        )
    )
    client = RecordingACPClient()
    adapter.on_connect(cast(AcpClient, client))
    session = _make_session(cwd=tmp_path)
    session.session_mode_id = "plan"

    asyncio.run(
        adapter._emit_plan_update(
            client=cast(AcpClient, client),
            session=session,
            entries=[PlanEntry(content="Inspect repo", status="pending", priority="high")],
        )
    )
    assert session.plan_entries == [
        {
            "content": "Inspect repo",
            "status": "pending",
            "priority": "high",
        }
    ]

    class _NoneConfigProvider:
        async def get_config_options(self, session: AcpSessionContext) -> None:
            del session
            return None

        async def set_config_option(
            self,
            session: AcpSessionContext,
            config_id: str,
            value: str | bool,
        ) -> None:
            del session, config_id, value
            return None

    options_adapter = _make_adapter(
        config=AdapterConfig(config_options_provider=_NoneConfigProvider())
    )
    assert asyncio.run(options_adapter._config_options(_make_session())) == []
    assert (
        asyncio.run(_NoneConfigProvider().set_config_option(_make_session(), "demo", True)) is None
    )

    class _NullModelsProvider:
        async def get_model_state(self, session: AcpSessionContext) -> ModelSelectionState:
            del session
            return ModelSelectionState(available_models=[], current_model_id="base")

        async def set_model(self, session: AcpSessionContext, model_id: str) -> ModelSelectionState:
            del session, model_id
            return ModelSelectionState(available_models=[], current_model_id=None)

    class _NullModesProvider:
        async def get_mode_state(self, session: AcpSessionContext) -> ModeState:
            del session
            return ModeState(modes=[], current_mode_id="ask")

        async def set_mode(self, session: AcpSessionContext, mode_id: str) -> ModeState:
            del session, mode_id
            return ModeState(modes=[], current_mode_id=None)

    provider_adapter = _make_adapter(
        config=AdapterConfig(
            models_provider=_NullModelsProvider(),
            modes_provider=_NullModesProvider(),
        )
    )
    provider_session = _make_session(cwd=tmp_path)
    assert (
        asyncio.run(_NullModelsProvider().get_model_state(provider_session)).current_model_id
        == "base"
    )
    assert (
        asyncio.run(_NullModesProvider().get_mode_state(provider_session)).current_mode_id == "ask"
    )
    assert asyncio.run(provider_adapter._set_model(provider_session, "ignored")) is None
    assert asyncio.run(provider_adapter._set_mode(provider_session, "ignored")) is None

    class _StructuredPlanModel(BaseModel):
        plan_md: str
        plan_entries: list[PlanEntry]

    structured_plan = TaskPlan(
        plan_md="# Plan",
        plan_entries=[PlanEntry(content="Inspect repo", status="pending", priority="high")],
    )
    assert adapter._task_plan_from_value(structured_plan) == structured_plan
    model_backed = adapter._task_plan_from_value(
        {"structured_response": _StructuredPlanModel(**structured_plan.model_dump(mode="python"))}
    )
    assert model_backed == structured_plan
    assert adapter._task_plan_from_value({"structured_response": {"bad": "payload"}}) is None


def test_runtime_server_and_root_surface_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = cast(Any, object())
    source = StaticGraphSource(graph=graph)
    assert _resolve_graph_source(graph=graph, graph_factory=None, graph_source=None) is not None
    assert _resolve_graph_source(graph=None, graph_factory=None, graph_source=source) is source

    async def factory(session: AcpSessionContext) -> Any:
        del session
        return graph

    resolved_factory = _resolve_graph_source(graph=None, graph_factory=factory, graph_source=None)
    assert isinstance(resolved_factory, FactoryGraphSource)
    assert asyncio.run(resolved_factory.get_graph(_make_session())) is graph

    with pytest.raises(ValueError):
        _resolve_graph_source(graph=None, graph_factory=None, graph_source=None)

    models_provider = _SyncModelsProvider(
        model_state=ModelSelectionState(
            available_models=[ModelInfo(model_id="base", name="Base")],
            current_model_id="base",
        )
    )
    modes_provider = _AsyncModesProvider(
        mode_state=ModeState(
            modes=[SessionMode(id="ask", name="Ask")],
            current_mode_id="ask",
        )
    )
    config_provider = _ConfigProvider(
        options=[
            SessionConfigOptionBoolean(
                id="safety",
                name="Safety",
                type="boolean",
                current_value=True,
            )
        ]
    )
    config = AdapterConfig(
        config_options_provider=config_provider,
        models_provider=models_provider,
        modes_provider=modes_provider,
    )
    resolved_config = _resolve_config(
        config=config,
        event_projection_maps=[StructuredEventProjectionMap()],
        graph_name="planner",
        projection_maps=[FileSystemProjectionMap()],
    )
    assert resolved_config.agent_name == "planner"
    assert tuple(resolved_config.projection_maps) != ()
    assert tuple(resolved_config.event_projection_maps) != ()
    assert resolved_config.config_options_provider is config_provider
    assert resolved_config.models_provider is models_provider
    assert resolved_config.modes_provider is modes_provider

    explicit_name_config = _resolve_config(
        config=AdapterConfig(agent_name="custom"),
        event_projection_maps=None,
        graph_name="ignored",
        projection_maps=None,
    )
    assert explicit_name_config.agent_name == "custom"

    captured: dict[str, Any] = {}

    async def fake_run_agent(agent: Any) -> None:
        captured["agent"] = agent

    monkeypatch.setattr("langchain_acp.runtime.server.run_agent", fake_run_agent)
    run_acp(graph=graph)
    assert captured["agent"] is not None
    assert (
        create_acp_agent(graph_source=source, config=AdapterConfig()).__class__.__name__
        == "LangChainAcpAgent"
    )


def test_langchain_adapter_lifecycle_replay_and_helper_paths(tmp_path: Path) -> None:
    config = AdapterConfig(
        available_models=[ModelInfo(model_id="gpt-5", name="GPT-5")],
        available_modes=[SessionMode(id="ask", name="Ask")],
    )
    adapter = _make_adapter(config=config)
    client = RecordingACPClient()
    adapter.on_connect(cast(AcpClient, client))

    initialize = asyncio.run(adapter.initialize(protocol_version=1))
    assert initialize.agent_info is not None
    assert initialize.agent_capabilities is not None
    assert initialize.agent_info.name == config.agent_name
    assert initialize.agent_capabilities.load_session is True

    mcp_servers = [
        McpServerStdio(name="stdio", command="python", args=["server.py"], env=[]),
        HttpMcpServer(name="http", url="https://example.com", headers=[], type="http"),
        SseMcpServer(name="sse", url="https://example.com/sse", headers=[], type="sse"),
    ]
    new_session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=mcp_servers))
    session_id = new_session.session_id
    assert new_session.models is not None
    assert new_session.modes is not None

    asyncio.run(
        adapter._emit_update(
            client=cast(AcpClient, client),
            session=adapter._require_session(session_id),
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(type="text", text="replay me"),
                message_id="m1",
            ),
        )
    )
    replay_count = len(client.updates)
    transcript_count = len(adapter._require_session(session_id).transcript)
    asyncio.run(
        adapter._emit_update(
            client=cast(AcpClient, client),
            session=adapter._require_session(session_id),
            update=SimpleNamespace(session_update="ignored"),
        )
    )
    assert len(adapter._require_session(session_id).transcript) == transcript_count

    assert (
        asyncio.run(adapter.load_session(cwd=str(tmp_path), session_id="missing", mcp_servers=[]))
        is None
    )

    load_response = asyncio.run(
        adapter.load_session(
            cwd=str(tmp_path / "loaded"),
            session_id=session_id,
            mcp_servers=[mcp_servers[0]],
        )
    )
    assert load_response is not None
    assert len(client.updates) > replay_count
    loaded_session = adapter._require_session(session_id)
    assert loaded_session.cwd == tmp_path / "loaded"
    assert loaded_session.mcp_servers[0]["name"] == "stdio"

    list_response = asyncio.run(adapter.list_sessions(cwd=str(tmp_path / "loaded")))
    assert [item.session_id for item in list_response.sessions] == [session_id]
    assert [item.session_id for item in asyncio.run(adapter.list_sessions()).sessions] == [
        session_id
    ]

    assert asyncio.run(adapter.set_session_model("gpt-5", session_id=session_id)) is not None
    assert asyncio.run(adapter.set_session_mode("ask", session_id=session_id)) is not None
    assert (
        asyncio.run(adapter.set_config_option("demo-flag", session_id=session_id, value=True))
        is not None
    )

    resume_response = asyncio.run(
        adapter.resume_session(
            cwd=str(tmp_path / "resumed"),
            session_id=session_id,
            mcp_servers=[mcp_servers[1]],
        )
    )
    assert resume_response.models is not None
    resumed_session = adapter._require_session(session_id)
    assert resumed_session.cwd == tmp_path / "resumed"
    assert resumed_session.client is client

    fork_response = asyncio.run(
        adapter.fork_session(
            cwd=str(tmp_path / "forked"),
            session_id=session_id,
            mcp_servers=[mcp_servers[2]],
        )
    )
    forked_session = adapter._require_session(fork_response.session_id)
    assert forked_session.cwd == tmp_path / "forked"
    assert forked_session.mcp_servers[0]["type"] == "sse"

    asyncio.run(adapter.close_session(session_id=fork_response.session_id))
    assert adapter._store.get(fork_response.session_id) is None

    asyncio.run(adapter.cancel(session_id=session_id))
    assert session_id in adapter._cancelled_sessions
    assert asyncio.run(adapter.authenticate(method_id="none")) is None

    with pytest.raises(RequestError):
        asyncio.run(adapter.ext_method("unknown.method", {}))
    with pytest.raises(RequestError):
        asyncio.run(adapter.ext_notification("unknown.notification", {}))
    with pytest.raises(RequestError):
        asyncio.run(adapter.set_session_model("missing", session_id=session_id))
    with pytest.raises(RequestError):
        asyncio.run(adapter.set_session_mode("missing", session_id=session_id))
    with pytest.raises(RequestError):
        adapter._require_session("missing")
    disconnected_adapter = _make_adapter(config=config)
    disconnected_session = _make_session(cwd=tmp_path, session_id="disconnected")
    disconnected_adapter._store.save(disconnected_session)
    assert disconnected_adapter._require_session("disconnected").client is None
    with pytest.raises(RequestError):
        disconnected_adapter._require_client()
    with pytest.raises(RequestError):
        asyncio.run(adapter.fork_session(cwd=str(tmp_path), session_id="missing", mcp_servers=[]))


def test_langchain_adapter_prompt_and_interrupt_helpers(tmp_path: Path) -> None:
    adapter = _make_adapter()
    client = RecordingACPClient()
    adapter.on_connect(cast(AcpClient, client))
    session = _make_session(cwd=tmp_path)

    asyncio.run(
        adapter._emit_user_prompt(
            client=cast(AcpClient, client),
            session=session,
            prompt=[TextContentBlock(type="text", text="Summarize the repo")],
            message_id="u1",
        )
    )
    assert session.title == "Summarize the repo"
    titled_session = _make_session(cwd=tmp_path, session_id="session-2")
    titled_session.title = "Existing title"
    asyncio.run(
        adapter._emit_user_prompt(
            client=cast(AcpClient, client),
            session=titled_session,
            prompt=[TextContentBlock(type="text", text="  ")],
            message_id="u2",
        )
    )
    assert titled_session.title == "Existing title"
    untitled_session = _make_session(cwd=tmp_path, session_id="session-3")
    asyncio.run(
        adapter._emit_user_prompt(
            client=cast(AcpClient, client),
            session=untitled_session,
            prompt=[ResourceContentBlock(type="resource_link", name="doc", uri="file:///doc")],
            message_id="u3",
        )
    )
    assert untitled_session.title is None

    generic_block = EmbeddedResourceContentBlock(
        type="resource",
        resource=BlobResourceContents(
            uri="file:///blob", mime_type="application/octet-stream", blob="AA=="
        ),
    )
    content = prompt_to_langchain_content(
        [
            TextContentBlock(type="text", text="hello"),
            ImageContentBlock(type="image", data="aGVsbG8=", mime_type="image/png"),
            AudioContentBlock(type="audio", data="d2F2", mime_type="audio/wav"),
            ResourceContentBlock(
                type="resource_link",
                name="doc",
                uri="file:///doc",
                description="Workspace guide",
            ),
            EmbeddedResourceContentBlock(
                type="resource",
                resource=TextResourceContents(
                    uri="file:///note",
                    text="note text",
                    mime_type="text/plain",
                ),
            ),
            EmbeddedResourceContentBlock(
                type="resource",
                resource=BlobResourceContents(
                    uri="file:///image.png",
                    mime_type="image/png",
                    blob="aGVsbG8=",
                ),
            ),
            generic_block,
        ]
    )
    assert content[0] == {"type": "text", "text": "hello"}
    assert content[1]["type"] == "image_url"
    assert content[2] == {"type": "audio", "base64": "d2F2", "mime_type": "audio/wav"}
    assert "Resource: doc" in content[3]["text"]
    assert "Description: Workspace guide" in content[3]["text"]
    assert content[4]["text"].endswith("note text")
    assert content[5]["type"] == "image_url"
    assert "file:///blob" in content[6]["text"]
    assert "Embedded binary payload" in content[6]["text"]
    assert (
        "user-message"
        in prompt_to_langchain_content(
            cast(list[Any], [_UnknownPromptBlock(payload="user-message")])
        )[0]["text"]
    )
    assert message_text(["a", {"type": "text", "text": "b"}]) == "ab"
    assert message_text("plain") == "plain"
    assert message_text({"bad": "shape"}) == ""
    assert (
        message_text([{"type": "image_url", "url": "ignored"}, {"type": "text", "text": 1}]) == ""
    )
    assert adapter._parse_json_object("") == {}
    assert adapter._parse_json_object('{"path":"demo"}') == {"path": "demo"}
    assert adapter._parse_json_object("[1,2]") == {}
    assert adapter._parse_json_object("{bad json") == {}
    projectable_output = {"stdout": "ok"}
    assert adapter._projectable_raw_output(projectable_output) is projectable_output
    assert adapter._projectable_raw_output('{"stdout":"ok"}') == {"stdout": "ok"}

    builder_graph = _BuilderGraph()
    compiled_graph = adapter._ensure_checkpointer(builder_graph)
    assert builder_graph.compile_calls
    assert compiled_graph["name"] == "demo-graph"
    builder_graph.checkpointer = object()
    assert adapter._ensure_checkpointer(builder_graph) is builder_graph
    plain_graph = object()
    assert adapter._ensure_checkpointer(plain_graph) is plain_graph
    assert adapter._ensure_checkpointer(_BuilderWithoutCompileGraph()) is not None

    active_tool_calls: dict[str, dict[str, Any]] = {}
    tool_call_accumulator: dict[int, dict[str, str | int | None]] = {}
    tool_chunk = AIMessageChunk(
        content="",
        tool_call_chunks=[
            {
                "id": "call-1",
                "name": "read_file",
                "args": '{"path":"notes.txt"}',
                "index": 0,
            }
        ],
    )
    asyncio.run(
        adapter._process_tool_call_chunks(
            client=cast(AcpClient, client),
            session=session,
            message_chunk=tool_chunk,
            active_tool_calls=active_tool_calls,
            tool_call_accumulator=tool_call_accumulator,
        )
    )
    assert "call-1" in active_tool_calls
    asyncio.run(
        adapter._process_tool_call_chunks(
            client=cast(AcpClient, client),
            session=session,
            message_chunk=cast(
                AIMessageChunk,
                SimpleNamespace(
                    tool_call_chunks=[
                        "bad",
                        {
                            "id": "call-1",
                            "name": "read_file",
                            "args": '{"path":"notes.txt"}',
                            "index": 0,
                        },
                        {"name": "late_name", "args": '{"path":"other.txt"}'},
                    ]
                ),
            ),
            active_tool_calls=active_tool_calls,
            tool_call_accumulator=tool_call_accumulator,
        )
    )
    asyncio.run(
        adapter._process_tool_call_chunks(
            client=cast(AcpClient, client),
            session=session,
            message_chunk=cast(
                AIMessageChunk,
                SimpleNamespace(
                    tool_call_chunks=[
                        {"id": "call-2", "name": object(), "args": 1, "index": 1},
                    ]
                ),
            ),
            active_tool_calls=active_tool_calls,
            tool_call_accumulator=tool_call_accumulator,
        )
    )
    assert "call-2" not in active_tool_calls

    asyncio.run(
        adapter._handle_tool_message(
            client=cast(AcpClient, client),
            session=session,
            message_chunk=ToolMessage(
                content="done",
                tool_call_id="call-1",
                status="success",
            ),
            active_tool_calls=active_tool_calls,
        )
    )
    assert "call-1" not in active_tool_calls
    asyncio.run(
        adapter._handle_tool_message(
            client=cast(AcpClient, client),
            session=session,
            message_chunk=cast(Any, SimpleNamespace(content="ignored", tool_call_id=None)),
            active_tool_calls=active_tool_calls,
        )
    )

    asyncio.run(
        adapter._process_message_chunk(
            client=cast(AcpClient, client),
            session=session,
            message_chunk="agent text",
            active_tool_calls={},
            tool_call_accumulator={},
        )
    )
    asyncio.run(
        adapter._process_message_chunk(
            client=cast(AcpClient, client),
            session=session,
            message_chunk="",
            active_tool_calls={},
            tool_call_accumulator={},
        )
    )
    asyncio.run(
        adapter._process_message_chunk(
            client=cast(AcpClient, client),
            session=session,
            message_chunk=AIMessageChunk(content=[{"type": "text", "text": "chunk text"}]),
            active_tool_calls={},
            tool_call_accumulator={},
        )
    )
    asyncio.run(
        adapter._process_message_chunk(
            client=cast(AcpClient, client),
            session=session,
            message_chunk=SimpleNamespace(content=[{"type": "other"}]),
            active_tool_calls={},
            tool_call_accumulator={},
        )
    )

    @dataclass
    class _ContentWrapper:
        content: list[dict[str, str]]

    asyncio.run(
        adapter._process_message_chunk(
            client=cast(AcpClient, client),
            session=session,
            message_chunk=_ContentWrapper(content=[{"type": "text", "text": "wrapped"}]),
            active_tool_calls={},
            tool_call_accumulator={},
        )
    )
    assert any(isinstance(update, AgentMessageChunk) for _, update in client.updates)

    projection_adapter = _make_adapter(
        config=AdapterConfig(event_projection_maps=[StructuredEventProjectionMap()])
    )
    projection_client = RecordingACPClient()
    projection_adapter.on_connect(cast(AcpClient, projection_client))
    projection_session = _make_session(cwd=tmp_path, session_id="projection")
    asyncio.run(
        projection_adapter._emit_projected_events(
            client=cast(AcpClient, projection_client),
            session=projection_session,
            payload={
                "node": {
                    "events": [
                        {
                            "type": "tool_call",
                            "toolCallId": "nested-1",
                            "title": "nested",
                            "kind": "other",
                            "status": "in_progress",
                        }
                    ]
                },
                "noop": {"ignored": True},
            },
        )
    )
    nested_updates = [
        update for _, update in projection_client.updates if isinstance(update, ToolCallStart)
    ]
    assert len(nested_updates) == 1

    assert (
        asyncio.run(
            adapter._handle_update_payload(
                client=cast(AcpClient, client), session=session, payload="bad"
            )
        )
        is None
    )
    with pytest.raises(RequestError):
        asyncio.run(
            adapter._handle_update_payload(
                client=cast(AcpClient, client),
                session=session,
                payload={"__interrupt__": "bad"},
            )
        )

    with pytest.raises(RequestError):
        asyncio.run(
            adapter._resolve_interrupts(
                client=cast(AcpClient, client),
                session=session,
                interrupts=[{"bad": "shape"}],
            )
        )

    no_approval_adapter = _make_adapter(config=AdapterConfig(approval_bridge=None))
    no_approval_adapter.on_connect(cast(AcpClient, client))
    with pytest.raises(RequestError):
        asyncio.run(
            no_approval_adapter._resolve_interrupts(
                client=cast(AcpClient, client),
                session=session,
                interrupts=[{"action_requests": [], "review_configs": []}],
            )
        )
    with pytest.raises(RequestError):
        asyncio.run(
            adapter._resolve_interrupts(
                client=cast(AcpClient, client),
                session=session,
                interrupts=[object()],
            )
        )

    cancelling_client = RecordingACPClient()
    cancelling_client.queue_permission_cancelled()
    approval_adapter = _make_adapter()
    approval_adapter.on_connect(cast(AcpClient, cancelling_client))
    with pytest.raises(RequestError):
        asyncio.run(
            approval_adapter._resolve_interrupts(
                client=cast(AcpClient, cancelling_client),
                session=session,
                interrupts=[
                    {
                        "action_requests": [{"name": "read_file", "args": {"path": "notes.txt"}}],
                        "review_configs": [{"action_name": "read_file"}],
                    }
                ],
            )
        )
    assert (
        asyncio.run(
            adapter._handle_update_payload(
                client=cast(AcpClient, client),
                session=session,
                payload={"node": {"todos": "bad"}},
            )
        )
        is None
    )

    asyncio.run(
        adapter._emit_plan_update(
            client=cast(AcpClient, client),
            session=session,
            entries=[
                PlanEntry(content="Inspect repo", status="completed", priority="high"),
                PlanEntry(content="Bad status", status="pending", priority="medium"),
            ],
        )
    )
    assert session.plan_entries == [
        {"content": "Inspect repo", "status": "completed", "priority": "high"},
        {"content": "Bad status", "status": "pending", "priority": "medium"},
    ]
    asyncio.run(
        adapter._emit_agent_text(
            client=cast(AcpClient, client),
            session=session,
            text="",
        )
    )
    assert adapter._derive_title([TextContentBlock(type="text", text=" hello ")]) == "hello"
    assert (
        adapter._derive_title(
            [
                TextContentBlock(type="text", text="   "),
                TextContentBlock(type="text", text="next title"),
            ]
        )
        == "next title"
    )
    assert (
        adapter._derive_title(
            [ResourceContentBlock(type="resource_link", name="doc", uri="file:///doc")]
        )
        is None
    )
    assert adapter._serialize_mcp_servers(None) == []

    replay_disabled_adapter = _make_adapter(config=AdapterConfig(replay_history_on_load=False))
    replay_disabled_adapter.on_connect(cast(AcpClient, client))
    replay_session = _make_session(cwd=tmp_path, session_id="replay-disabled")
    replay_session.transcript.append(
        StoredSessionUpdate.from_update(
            AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(type="text", text="ignore replay"),
                message_id="m-disabled",
            )
        )
    )
    replay_count = len(client.updates)
    asyncio.run(replay_disabled_adapter._replay_transcript(replay_session))
    assert len(client.updates) == replay_count

    explicit_default_adapter = _make_adapter(
        config=AdapterConfig(
            available_models=[ModelInfo(model_id="base", name="Base")],
            default_model_id="explicit-model",
            available_modes=[SessionMode(id="ask", name="Ask")],
            default_mode_id="explicit-mode",
        )
    )
    assert explicit_default_adapter._default_model_id() == "explicit-model"
    assert explicit_default_adapter._default_mode_id() == "explicit-mode"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(explicit_default_adapter, "_default_model_id", lambda: None)
    monkeypatch.setattr(explicit_default_adapter, "_default_mode_id", lambda: None)
    try:
        no_current_session = _make_session(cwd=tmp_path, session_id="no-current")
        resolved_model_state = asyncio.run(
            explicit_default_adapter._model_state(no_current_session)
        )
        resolved_mode_state = asyncio.run(explicit_default_adapter._mode_state(no_current_session))
        assert resolved_model_state is not None
        assert resolved_model_state.current_model_id == "explicit-model"
        assert resolved_mode_state is not None
        assert resolved_mode_state.current_mode_id == "explicit-mode"
        option_ids = [
            option.id
            for option in asyncio.run(explicit_default_adapter._config_options(no_current_session))
        ]
        assert option_ids == ["mode", "model"]
    finally:
        monkeypatch.undo()


def test_phase3_provider_state_helpers_cover_sync_async_and_reserved_config_paths(
    tmp_path: Path,
) -> None:
    models_provider = _SyncModelsProvider(
        model_state=ModelSelectionState(
            available_models=[ModelInfo(model_id="base", name="Base")],
            current_model_id="base",
            config_option_name="Graph Model",
        )
    )
    modes_provider = _AsyncModesProvider(
        mode_state=ModeState(
            modes=[
                SessionMode(id="ask", name="Ask"),
                SessionMode(id="plan", name="Plan"),
            ],
            current_mode_id="ask",
            config_option_name="Graph Mode",
        )
    )
    config_provider = _ConfigProvider(
        options=[
            SessionConfigOptionBoolean(
                id="safe_tools",
                name="Safe Tools",
                type="boolean",
                current_value=True,
            )
        ]
    )
    adapter = _make_adapter(
        config=AdapterConfig(
            config_options_provider=config_provider,
            models_provider=models_provider,
            modes_provider=modes_provider,
        )
    )
    client = RecordingACPClient()
    adapter.on_connect(cast(AcpClient, client))

    created = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    assert created.models is not None
    assert created.models.current_model_id == "base"
    assert created.modes is not None
    assert created.modes.current_mode_id == "ask"
    assert created.config_options is not None
    assert [option.id for option in created.config_options] == [
        "mode",
        "model",
        "safe_tools",
    ]
    assert created.config_options[0].name == "Graph Mode"
    assert created.config_options[1].name == "Graph Model"

    session = adapter._require_session(created.session_id)
    assert session.session_model_id == "base"
    assert session.session_mode_id == "ask"

    assert asyncio.run(adapter.set_session_model("pro", session_id=created.session_id)) is not None
    assert asyncio.run(adapter.set_session_mode("plan", session_id=created.session_id)) is not None
    assert models_provider.set_calls == ["pro"]
    assert modes_provider.set_calls == ["plan"]
    session = adapter._require_session(created.session_id)
    assert session.session_model_id == "pro"
    assert session.session_mode_id == "plan"
    assert session.config_values["provider-model"] == "pro"
    assert session.config_values["provider-mode"] == "plan"

    response = asyncio.run(
        adapter.set_config_option("safe_tools", session_id=created.session_id, value=False)
    )
    assert response is not None
    assert config_provider.set_calls == [("safe_tools", False)]
    session = adapter._require_session(created.session_id)
    assert session.config_values["safe_tools"] is False

    model_response = asyncio.run(
        adapter.set_config_option("model", session_id=created.session_id, value="ultra")
    )
    assert model_response is not None
    mode_response = asyncio.run(
        adapter.set_config_option("mode", session_id=created.session_id, value="ask")
    )
    assert mode_response is not None
    session = adapter._require_session(created.session_id)
    assert session.session_model_id == "ultra"
    assert session.session_mode_id == "ask"

    plan_adapter = _make_adapter(
        config=AdapterConfig(
            available_modes=[SessionMode(id="plan", name="Plan")],
            default_mode_id="plan",
            plan_mode_id="plan",
        )
    )
    plan_created = asyncio.run(plan_adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    plan_response = asyncio.run(
        plan_adapter.set_config_option(
            "plan_generation_type",
            session_id=plan_created.session_id,
            value="tools",
        )
    )
    assert plan_response is not None
    plan_session = plan_adapter._require_session(plan_created.session_id)
    assert plan_session.config_values["plan_generation_type"] == "tools"
    with pytest.raises(RequestError):
        asyncio.run(
            plan_adapter.set_config_option(
                "plan_generation_type",
                session_id=plan_created.session_id,
                value="bad",
            )
        )

    disabled_options_adapter = _make_adapter(
        config=AdapterConfig(
            models_provider=_SyncModelsProvider(
                model_state=ModelSelectionState(
                    available_models=[ModelInfo(model_id="hidden", name="Hidden")],
                    current_model_id="hidden",
                    enable_config_option=False,
                )
            ),
            modes_provider=_AsyncModesProvider(
                mode_state=ModeState(
                    modes=[SessionMode(id="hidden-mode", name="Hidden Mode")],
                    current_mode_id="hidden-mode",
                    enable_config_option=False,
                )
            ),
        )
    )
    hidden_session = _make_session(cwd=tmp_path, session_id="hidden")
    assert asyncio.run(disabled_options_adapter._config_options(hidden_session)) == []

    rejected_adapter = _make_adapter(
        config=AdapterConfig(
            models_provider=_SyncModelsProvider(model_state=None),
            modes_provider=_AsyncModesProvider(mode_state=None),
        )
    )
    rejected_session = _make_session(cwd=tmp_path, session_id="rejected")
    rejected_adapter._store.save(rejected_session)
    with pytest.raises(RequestError):
        asyncio.run(rejected_adapter.set_session_model("missing", session_id="rejected"))
    with pytest.raises(RequestError):
        asyncio.run(rejected_adapter.set_session_mode("missing", session_id="rejected"))
    with pytest.raises(RequestError):
        asyncio.run(rejected_adapter.set_config_option("model", session_id="rejected", value="bad"))
    with pytest.raises(RequestError):
        asyncio.run(rejected_adapter.set_config_option("mode", session_id="rejected", value="bad"))

    @dataclass(slots=True, kw_only=True)
    class _NonMutatingConfigProvider:
        calls: list[tuple[str, str | bool]] = field(default_factory=list)

        async def get_config_options(self, session: AcpSessionContext) -> list[ConfigOption]:
            del session
            return []

        async def set_config_option(
            self,
            session: AcpSessionContext,
            config_id: str,
            value: str | bool,
        ) -> None:
            self.calls.append((config_id, value))
            session.config_values["provider-echo"] = value
            return None

    non_mutating_provider = _NonMutatingConfigProvider()
    fallback_adapter = _make_adapter(
        config=AdapterConfig(config_options_provider=cast(Any, non_mutating_provider))
    )
    fallback_created = asyncio.run(fallback_adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    fallback_session_before = fallback_adapter._require_session(fallback_created.session_id)
    previous_updated_at = fallback_session_before.updated_at
    fallback_response = asyncio.run(
        fallback_adapter.set_config_option(
            "demo-flag",
            session_id=fallback_created.session_id,
            value=True,
        )
    )
    assert fallback_response is not None
    fallback_session_after = fallback_adapter._require_session(fallback_created.session_id)
    assert fallback_session_after.config_values["demo-flag"] is True
    assert fallback_session_after.config_values["provider-echo"] is True
    assert fallback_session_after.updated_at >= previous_updated_at


def test_langchain_adapter_prompt_resume_and_stream_edge_paths(tmp_path: Path) -> None:
    approval_bridge = _FixedApprovalBridge(decisions=[{"type": "approve"}])
    graph = _StreamingGraph(
        streams=[
            [
                "bad tuple",
                (
                    (),
                    "updates",
                    {
                        "__interrupt__": [
                            SimpleNamespace(
                                value={
                                    "action_requests": [
                                        {
                                            "name": "delete_file",
                                            "args": {"path": "draft.txt"},
                                        }
                                    ],
                                    "review_configs": [{"action_name": "delete_file"}],
                                }
                            )
                        ]
                    },
                ),
            ],
            [
                (
                    (),
                    "messages",
                    (AIMessageChunk(content=[{"type": "text", "text": "resumed"}]), {}),
                )
            ],
        ]
    )
    adapter = cast(
        LangChainAcpAgent,
        create_acp_agent(
            graph=cast(Any, graph),
            config=AdapterConfig(approval_bridge=cast(Any, approval_bridge)),
        ),
    )
    client = RecordingACPClient()
    adapter.on_connect(cast(AcpClient, client))
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    response = asyncio.run(
        adapter.prompt(
            prompt=[TextContentBlock(type="text", text="resume me")],
            session_id=session.session_id,
        )
    )

    assert response.stop_reason == "end_turn"
    assert len(approval_bridge.calls) == 1
    assert len(graph.inputs) == 2
    assert isinstance(graph.inputs[1], Command)
    assert agent_message_texts(client) == ["resumed"]


def test_langchain_adapter_prompt_cancels_mid_stream(tmp_path: Path) -> None:
    session_holder: dict[str, str] = {}

    @dataclass(slots=True)
    class _CancellingGraph:
        async def astream(
            self,
            stream_input: Any,
            *,
            config: Any,
            stream_mode: Any,
            subgraphs: bool,
        ):
            del stream_input, config, stream_mode, subgraphs
            adapter._cancelled_sessions.add(session_holder["session_id"])
            yield ((), "messages", (AIMessageChunk(content="ignored"), {}))

    adapter = cast(
        LangChainAcpAgent,
        create_acp_agent(graph=cast(Any, _CancellingGraph()), config=AdapterConfig()),
    )
    client = RecordingACPClient()
    adapter.on_connect(cast(AcpClient, client))
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    session_holder["session_id"] = session.session_id

    response = asyncio.run(
        adapter.prompt(
            prompt=[TextContentBlock(type="text", text="cancel during stream")],
            session_id=session.session_id,
        )
    )

    assert response.stop_reason == "cancelled"


def test_langchain_adapter_cancelled_prompt_returns_cancelled(tmp_path: Path) -> None:
    def read_file(path: str) -> str:
        """Read a file from the workspace."""
        return f"ok:{path}"

    assert read_file("demo.txt") == "ok:demo.txt"

    from langchain.agents import create_agent

    graph = create_agent(
        model=GenericFakeChatModel(messages=iter([])),
        tools=[read_file],
        name="cancel-demo",
    )
    adapter = create_acp_agent(graph=graph, config=AdapterConfig())
    client = RecordingACPClient()
    adapter.on_connect(cast(AcpClient, client))
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(adapter.cancel(session_id=session.session_id))

    response = asyncio.run(
        adapter.prompt(
            prompt=[TextContentBlock(type="text", text="cancel now")],
            session_id=session.session_id,
        )
    )

    assert response.stop_reason == "cancelled"
