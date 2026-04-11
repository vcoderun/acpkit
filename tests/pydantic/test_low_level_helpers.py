from __future__ import annotations as _annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from acp.exceptions import RequestError
from acp.helpers import text_block
from acp.schema import (
    AllowedOutcome,
    AudioContentBlock,
    ContentToolCallContent,
    CreateTerminalResponse,
    EmbeddedResourceContentBlock,
    EnvVariable,
    ImageContentBlock,
    KillTerminalResponse,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    RequestPermissionResponse,
    ResourceContentBlock,
    SessionConfigOptionBoolean,
    SessionInfoUpdate,
    SessionMode,
    TerminalOutputResponse,
    TextResourceContents,
    ToolCallStart,
    UserMessageChunk,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)
from pydantic import BaseModel
from pydantic_acp.agent_source import AgentFactory, AgentSource
from pydantic_acp.approvals import NativeApprovalBridge
from pydantic_acp.bridges.base import BufferedCapabilityBridge, CapabilityBridge
from pydantic_acp.bridges.history_processor import HistoryProcessorBridge
from pydantic_acp.bridges.prepare_tools import PrepareToolsBridge, PrepareToolsMode
from pydantic_acp.config import AdapterConfig
from pydantic_acp.hook_projection import HookEvent, HookProjectionMap
from pydantic_acp.host.context import ClientHostContext
from pydantic_acp.host.filesystem import ClientFilesystemBackend, FilesystemBackend
from pydantic_acp.host.terminal import ClientTerminalBackend, TerminalBackend
from pydantic_acp.models import AdapterModel
from pydantic_acp.projection import (
    CompositeProjectionMap,
    DefaultToolClassifier,
    FileSystemProjectionMap,
    ProjectionMap,
    ToolClassifier,
    ToolProjection,
    _bash_progress_content,
    _bash_status,
    _candidate_keys,
    _format_command_title,
    _merge_tool_projections,
    _preserve_file_diff_content,
    _preview_text,
    _read_existing_text,
    _single_line_preview,
    _stringify_value,
    build_tool_progress_update,
    build_tool_start_update,
    build_tool_updates,
    compose_projection_maps,
    extract_tool_call_locations,
)
from pydantic_acp.providers import (
    ApprovalStateProvider,
    ConfigOptionsProvider,
    ModelSelectionState,
    ModeState,
    PlanProvider,
    SessionModelsProvider,
    SessionModesProvider,
)
from pydantic_acp.runtime import server as server_module
from pydantic_acp.runtime.bridge_manager import BridgeManager
from pydantic_acp.runtime.prompts import (
    build_error_history,
    contains_deferred_tool_requests,
    derive_title,
    dump_message_history,
    load_message_history,
    prompt_to_text,
    sanitize_message_history,
    usage_from_run,
)
from pydantic_acp.runtime.server import _resolve_config
from pydantic_acp.runtime.session_surface import (
    build_mode_config_option,
    build_mode_state_from_selection,
    build_model_config_option,
    build_model_state_from_selection,
    find_model_option,
)
from pydantic_acp.serialization import DefaultOutputSerializer
from pydantic_acp.session.state import (
    AcpSessionContext,
    StoredSessionUpdate,
    _coerce_json_object,
    _coerce_json_value,
    _is_transcript_kind,
    utc_now,
)
from pydantic_acp.session.store import FileSessionStore, MemorySessionStore, SessionStore
from pydantic_ai import Agent, ModelRequest, ModelResponse, RunUsage, TextPart
from pydantic_ai.messages import (
    ModelMessage,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import DeferredToolRequests, RunContext, ToolApproved, ToolDenied
from typing_extensions import Sentinel

from .support import RecordingClient

_LOW_LEVEL_SENTINEL = Sentinel("_LOW_LEVEL_SENTINEL")


def _session(tmp_path: Path, *, session_id: str = "session-1") -> AcpSessionContext:
    now = utc_now()
    return AcpSessionContext(
        session_id=session_id,
        cwd=tmp_path,
        created_at=now,
        updated_at=now,
    )


def test_default_output_serializer_covers_special_cases() -> None:
    class DemoModel(BaseModel):
        value: int

    @dataclass
    class DemoData:
        value: int

    class DemoObject:
        def __repr__(self) -> str:
            return "demo-object"

    serializer = DefaultOutputSerializer()

    assert serializer.serialize(b"hi\xff") == "hi\ufffd"
    assert '"value": 1' in serializer.serialize(DemoModel(value=1))
    assert serializer.serialize(DemoData(value=2)) == '{\n  "value": 2\n}'
    assert serializer.serialize(DemoObject()) == "demo-object"


def test_client_host_context_from_bound_session_requires_client(tmp_path: Path) -> None:
    session = _session(tmp_path)
    with pytest.raises(ValueError):
        ClientHostContext.from_bound_session(session)

    client = RecordingClient()
    session.client = client
    context = ClientHostContext.from_bound_session(session)

    assert context.client is client
    assert context.session is session
    assert context.filesystem.session is session
    assert context.terminal.session is session


def test_protocol_contracts_and_client_backends_cover_interfaces(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path, session_id="protocols")

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

        async def read_text_file(self, *args: Any, **kwargs: Any) -> ReadTextFileResponse:
            self.calls.append(("read_text_file", args, dict(kwargs)))
            return ReadTextFileResponse(content="hello")

        async def write_text_file(self, *args: Any, **kwargs: Any) -> WriteTextFileResponse:
            self.calls.append(("write_text_file", args, dict(kwargs)))
            return WriteTextFileResponse()

        async def create_terminal(self, *args: Any, **kwargs: Any) -> CreateTerminalResponse:
            self.calls.append(("create_terminal", args, dict(kwargs)))
            return CreateTerminalResponse(terminal_id="term-1")

        async def terminal_output(self, *args: Any, **kwargs: Any) -> TerminalOutputResponse:
            self.calls.append(("terminal_output", args, dict(kwargs)))
            return TerminalOutputResponse(output="ok", truncated=False)

        async def release_terminal(self, *args: Any, **kwargs: Any) -> ReleaseTerminalResponse:
            self.calls.append(("release_terminal", args, dict(kwargs)))
            return ReleaseTerminalResponse()

        async def wait_for_terminal_exit(
            self, *args: Any, **kwargs: Any
        ) -> WaitForTerminalExitResponse:
            self.calls.append(("wait_for_terminal_exit", args, dict(kwargs)))
            return WaitForTerminalExitResponse(exit_code=0)

        async def kill_terminal(self, *args: Any, **kwargs: Any) -> KillTerminalResponse:
            self.calls.append(("kill_terminal", args, dict(kwargs)))
            return KillTerminalResponse()

    client = FakeClient()
    filesystem = ClientFilesystemBackend(client=cast(Any, client), session=session)
    terminal = ClientTerminalBackend(client=cast(Any, client), session=session)

    assert asyncio.run(filesystem.read_text_file("note.txt", limit=3, line=1)).content == "hello"
    assert asyncio.run(filesystem.write_text_file("note.txt", "body")) is not None
    assert (
        asyncio.run(
            terminal.create_terminal(
                "python",
                args=["-V"],
                cwd=str(tmp_path),
                env=[EnvVariable(name="X", value="1")],
                output_byte_limit=10,
            )
        ).terminal_id
        == "term-1"
    )
    assert asyncio.run(terminal.terminal_output("term-1")).output == "ok"
    assert asyncio.run(terminal.release_terminal("term-1")) is not None
    assert asyncio.run(terminal.wait_for_terminal_exit("term-1")).exit_code == 0
    assert asyncio.run(terminal.kill_terminal("term-1")) is not None

    assert client.calls[0][0] == "read_text_file"
    assert client.calls[0][2]["session_id"] == "protocols"
    assert client.calls[2][0] == "create_terminal"
    assert client.calls[2][2]["session_id"] == "protocols"

    assert cast(Any, AgentFactory.__call__)(_LOW_LEVEL_SENTINEL, session) is None
    assert asyncio.run(cast(Any, AgentSource.get_agent)(_LOW_LEVEL_SENTINEL, session)) is None
    assert (
        asyncio.run(
            cast(Any, AgentSource.get_deps)(_LOW_LEVEL_SENTINEL, session, _LOW_LEVEL_SENTINEL)
        )
        is None
    )
    assert (
        asyncio.run(cast(Any, FilesystemBackend.read_text_file)(_LOW_LEVEL_SENTINEL, "x")) is None
    )
    assert (
        asyncio.run(cast(Any, FilesystemBackend.write_text_file)(_LOW_LEVEL_SENTINEL, "x", "y"))
        is None
    )
    assert (
        asyncio.run(cast(Any, TerminalBackend.create_terminal)(_LOW_LEVEL_SENTINEL, "bash")) is None
    )
    assert asyncio.run(cast(Any, TerminalBackend.terminal_output)(_LOW_LEVEL_SENTINEL, "t")) is None
    assert (
        asyncio.run(cast(Any, TerminalBackend.release_terminal)(_LOW_LEVEL_SENTINEL, "t")) is None
    )
    assert (
        asyncio.run(cast(Any, TerminalBackend.wait_for_terminal_exit)(_LOW_LEVEL_SENTINEL, "t"))
        is None
    )
    assert asyncio.run(cast(Any, TerminalBackend.kill_terminal)(_LOW_LEVEL_SENTINEL, "t")) is None
    assert (
        cast(Any, SessionModelsProvider.get_model_state)(
            _LOW_LEVEL_SENTINEL, session, _LOW_LEVEL_SENTINEL
        )
        is None
    )
    assert (
        cast(Any, SessionModelsProvider.set_model)(
            _LOW_LEVEL_SENTINEL, session, _LOW_LEVEL_SENTINEL, "m"
        )
        is None
    )
    assert (
        cast(Any, SessionModesProvider.get_mode_state)(
            _LOW_LEVEL_SENTINEL, session, _LOW_LEVEL_SENTINEL
        )
        is None
    )
    assert (
        cast(Any, SessionModesProvider.set_mode)(
            _LOW_LEVEL_SENTINEL, session, _LOW_LEVEL_SENTINEL, "plan"
        )
        is None
    )
    assert (
        cast(Any, ConfigOptionsProvider.get_config_options)(
            _LOW_LEVEL_SENTINEL, session, _LOW_LEVEL_SENTINEL
        )
        is None
    )
    assert (
        cast(Any, ConfigOptionsProvider.set_config_option)(
            _LOW_LEVEL_SENTINEL,
            session,
            _LOW_LEVEL_SENTINEL,
            "id",
            True,
        )
        is None
    )
    assert (
        cast(Any, PlanProvider.get_plan)(_LOW_LEVEL_SENTINEL, session, _LOW_LEVEL_SENTINEL) is None
    )
    assert (
        cast(Any, ApprovalStateProvider.get_approval_state)(
            _LOW_LEVEL_SENTINEL, session, _LOW_LEVEL_SENTINEL
        )
        is None
    )
    assert cast(Any, SessionStore.delete)(_LOW_LEVEL_SENTINEL, "missing") is None
    assert (
        cast(Any, SessionStore.fork)(
            _LOW_LEVEL_SENTINEL,
            "missing",
            new_session_id="copy",
            cwd=tmp_path,
        )
        is None
    )
    assert cast(Any, SessionStore.get)(_LOW_LEVEL_SENTINEL, "missing") is None
    assert cast(Any, SessionStore.list_sessions)(_LOW_LEVEL_SENTINEL) is None
    assert cast(Any, SessionStore.save)(_LOW_LEVEL_SENTINEL, session) is None


def test_file_session_store_covers_delete_fork_missing_and_list_skip(
    tmp_path: Path,
) -> None:
    store = FileSessionStore(tmp_path / "sessions")
    first = _session(tmp_path, session_id="first")
    first.title = "First"
    store.save(first)
    assert store._session_path("first").exists()

    forked = store.fork("first", new_session_id="forked", cwd=tmp_path / "forked")
    assert forked is not None
    assert forked.session_id == "forked"
    assert forked.cwd == tmp_path / "forked"

    store.delete("first")
    assert not store._session_path("first").exists()
    store.delete("first")
    assert store.get("missing") is None
    assert store.fork("missing", new_session_id="nope", cwd=tmp_path) is None

    keep = _session(tmp_path, session_id="keep")
    keep.updated_at = utc_now()
    store.save(keep)

    @dataclass(slots=True)
    class SkipGhostFileSessionStore(FileSessionStore):
        def get(self, session_id: str) -> AcpSessionContext | None:
            if session_id == "ghost":
                return None
            return FileSessionStore.get(self, session_id)

    skip_store = SkipGhostFileSessionStore(store.root)
    ghost_path = skip_store._session_path("ghost")
    ghost_path.write_text("{}", encoding="utf-8")
    assert [session.session_id for session in skip_store.list_sessions()] == [
        "keep",
        "forked",
    ]

    memory = MemorySessionStore()
    assert memory.get("missing") is None
    assert memory.fork("missing", new_session_id="copy", cwd=tmp_path) is None
    memory.delete("missing")


def test_capability_bridge_defaults_and_buffered_failed_event(tmp_path: Path) -> None:
    session = _session(tmp_path)
    bridge = CapabilityBridge()
    agent = Agent(TestModel())

    assert bridge.drain_updates(session, agent) is None
    assert bridge.get_config_options(session, agent) is None
    assert bridge.get_mcp_capabilities(agent) is None
    assert bridge.get_approval_policy_key("tool") is None
    assert bridge.get_mode_state(session, agent) is None
    assert bridge.get_session_metadata(session, agent) is None
    assert bridge.get_tool_kind("tool") is None
    assert bridge.set_config_option(session, agent, "id", "value") is None
    assert bridge.set_mode(session, agent, "mode") is None

    buffered = BufferedCapabilityBridge()
    buffered._record_failed_event(session, title="failed.event", raw_output="boom")
    updates = buffered.drain_updates(session, agent)
    assert updates is not None
    assert len(updates) == 2


def test_history_processor_bridge_covers_duplicate_names_and_failures(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    bridge = HistoryProcessorBridge()
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart("hi")])]

    def plain(messages):
        del messages
        raise RuntimeError("plain boom")

    async def contextual(ctx, messages):
        del ctx, messages
        raise RuntimeError("ctx boom")

    wrapped_plain = bridge.wrap_plain_processor(session, plain, name="dup")
    bridge.wrap_plain_processor(session, plain, name="dup")
    wrapped_contextual = bridge.wrap_contextual_processor(session, contextual, name="ctx")

    async def run_plain() -> None:
        await wrapped_plain(messages)

    async def run_contextual() -> None:
        await wrapped_contextual(cast(RunContext[None], None), messages)

    with pytest.raises(RuntimeError, match="plain boom"):
        asyncio.run(run_plain())
    with pytest.raises(RuntimeError, match="ctx boom"):
        asyncio.run(run_contextual())

    assert bridge.processor_names == ["dup", "ctx"]
    updates = bridge.drain_updates(session, Agent(TestModel()))
    assert updates is not None
    assert len(updates) == 4


def test_prepare_tools_bridge_covers_validation_none_and_require_mode(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="at least one mode"):
        PrepareToolsBridge[None](default_mode_id="x", modes=[])
    with pytest.raises(ValueError, match="default mode"):
        PrepareToolsBridge[None](
            default_mode_id="missing",
            modes=[PrepareToolsMode(id="chat", name="Chat", prepare_func=lambda _c, t: t)],
        )

    bridge = PrepareToolsBridge[None](
        default_mode_id="chat",
        modes=[PrepareToolsMode(id="chat", name="Chat", prepare_func=lambda _c, _t: None)],
    )
    session = _session(tmp_path)

    async def run_prepare() -> Any:
        prepared = bridge.build_prepare_tools(session)(cast(RunContext[None], None), [])
        if asyncio.iscoroutine(prepared):
            return await prepared
        assert prepared is not None
        return prepared

    prepared = asyncio.run(run_prepare())
    assert prepared == []
    assert bridge.current_mode(session).id == "chat"
    assert bridge.is_plan_mode(session) is False
    with pytest.raises(ValueError, match="Unknown prepare-tools mode"):
        bridge._require_mode("missing")
    assert bridge.set_mode(session, Agent(TestModel()), "missing") is None


def test_hook_projection_map_covers_hidden_fallbacks_and_truncation() -> None:
    hidden = HookProjectionMap(hidden_event_ids=frozenset({"hidden"}))
    hidden_event = HookEvent(
        event_id="hidden",
        hook_name="hidden",
        tool_name=None,
        tool_filters=(),
        status="completed",
    )
    assert hidden.build_start_update(tool_call_id="1", event=hidden_event) is None
    assert hidden.build_progress_update(tool_call_id="1", event=hidden_event) is None

    projection = HookProjectionMap(
        event_labels={},
        event_kinds={},
        include_raw_input=False,
        max_output_chars=4,
    )
    event = HookEvent(
        event_id="custom_event",
        hook_name="custom_hook",
        tool_name=None,
        tool_filters=("a",),
        raw_output="abcdef",
        status="completed",
    )
    start = projection.build_start_update(tool_call_id="1", event=event)
    progress = projection.build_progress_update(tool_call_id="1", event=event)
    pending = projection.build_progress_update(
        tool_call_id="1",
        event=HookEvent(
            event_id="custom_event",
            hook_name="custom_hook",
            tool_name=None,
            tool_filters=(),
            status=None,
        ),
    )

    assert start is not None
    assert start.title == "Hook Custom Event (custom_hook)"
    assert start.kind == "execute"
    assert start.raw_input is None
    assert progress is not None
    assert progress.raw_output == "abcd\n\n...[truncated]"
    assert pending is None

    quiet_projection = HookProjectionMap(
        include_raw_output=False,
        show_hook_name_in_title=False,
        show_tool_name_in_title=False,
    )
    quiet_event = HookEvent(
        event_id="before_tool_execute",
        hook_name="before_tool_execute",
        tool_name="echo",
        tool_filters=(),
        raw_output="done",
        status="completed",
    )
    start_update, progress_update = quiet_projection.build_updates(
        tool_call_id="2",
        event=quiet_event,
    )
    assert start_update is not None
    assert start_update.title == "Hook Before Tool"
    assert progress_update is not None
    assert progress_update.raw_output is None


def test_prompt_history_handles_resources_and_usage_paths() -> None:
    prompt = cast(
        list[Any],
        [
            ResourceContentBlock(type="resource_link", name="doc", uri="file:///doc"),
            EmbeddedResourceContentBlock(
                type="resource",
                resource=TextResourceContents(uri="file:///note", text="hello"),
            ),
            ImageContentBlock(type="image", data="x", mime_type="image/png"),
            AudioContentBlock(type="audio", data="x", mime_type="audio/wav"),
        ],
    )
    assert derive_title([]) == "Untitled session"
    assert "[resource:doc] file:///doc" in prompt_to_text(prompt)
    assert "hello" in prompt_to_text(prompt)
    assert "[image]" in prompt_to_text(prompt)
    assert "[audio]" in prompt_to_text(prompt)
    assert contains_deferred_tool_requests([str, DeferredToolRequests]) is True
    assert contains_deferred_tool_requests([str, int]) is False
    assert load_message_history(None) == []
    assert usage_from_run(RunUsage()) is None

    unresolved: list[ModelMessage] = [
        ModelResponse(
            parts=[
                TextPart("keep"),
                ToolCallPart("demo_tool", {}, tool_call_id="call-1"),
            ]
        )
    ]
    sanitized = sanitize_message_history(unresolved, error_text="traceback here")
    assert len(sanitized) == 2
    assert isinstance(sanitized[0], ModelResponse)
    assert isinstance(sanitized[1], ModelResponse)
    assert isinstance(sanitized[1].parts[0], TextPart)
    assert "traceback here" in sanitized[1].parts[0].content

    history = dump_message_history(
        [
            ModelResponse(
                parts=[TextPart("The previous run failed before completion.\n\nTraceback:\nboom")]
            )
        ]
    )
    error_history = build_error_history(
        history,
        prompt_text="",
        traceback_text="boom",
    )
    loaded = load_message_history(error_history)
    texts = [
        part.content
        for message in loaded
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, TextPart)
    ]
    assert sum("boom" in text for text in texts) == 1


def test_session_state_round_trips_and_rejects_invalid_payloads(tmp_path: Path) -> None:
    assert _is_transcript_kind("tool_call")
    assert not _is_transcript_kind("nope")

    update = StoredSessionUpdate.from_update(
        UserMessageChunk(
            session_update="user_message_chunk",
            content=text_block("hello"),
            message_id="msg-1",
        )
    )
    restored = update.to_update()
    assert restored.session_update == "user_message_chunk"
    session_info_update = StoredSessionUpdate.from_update(
        SessionInfoUpdate(
            session_update="session_info_update",
            title="Demo",
            updated_at=utc_now().isoformat(),
            field_meta={"demo": {"ok": True}},
        )
    )
    assert session_info_update.to_update().session_update == "session_info_update"

    class BadUpdate:
        def model_dump(self, **kwargs):
            del kwargs
            return {"nope": "missing"}

    with pytest.raises(TypeError, match="missing `sessionUpdate`"):
        StoredSessionUpdate.from_update(cast(Any, BadUpdate()))
    with pytest.raises(TypeError, match="Expected a JSON object"):
        _coerce_json_object("x")
    with pytest.raises(TypeError, match="keys must be strings"):
        _coerce_json_object({1: "x"})
    with pytest.raises(TypeError, match="Unsupported JSON value"):
        _coerce_json_value(_LOW_LEVEL_SENTINEL)
    with pytest.raises(AssertionError):
        StoredSessionUpdate(kind=cast(Any, "unknown"), payload={}).to_update()


def test_session_surface_helpers_handle_missing_state_and_errors() -> None:
    model = AdapterModel(model_id="demo", name="Demo", override="demo")
    with pytest.raises(RequestError):
        build_model_config_option(
            ModelSelectionState(
                current_model_id=None,
                config_option_name="Model",
                config_option_description="Select",
                available_models=[model],
            )
        )
    with pytest.raises(RequestError):
        build_mode_config_option(ModeState(current_mode_id=None, modes=[]))

    assert build_model_state_from_selection(None) is None
    assert build_mode_state_from_selection(None) is None
    assert build_mode_state_from_selection(ModeState(current_mode_id=None, modes=[])) is None
    assert find_model_option("missing", available_models=[model]) is None
    mode_state = build_mode_state_from_selection(
        ModeState(
            current_mode_id="plan",
            modes=[SessionMode(id="plan", name="Plan", description="d")],
        )
    )
    assert mode_state is not None
    assert mode_state.current_mode_id == "plan"


def test_server_helpers_resolve_config_and_dispatch_run_acp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection = FileSystemProjectionMap(default_read_tool="read_file")
    hook_projection = HookProjectionMap()

    resolved = _resolve_config(
        config=AdapterConfig(agent_name="custom"),
        agent_name="agent-name",
        projection_maps=[projection, hook_projection],
    )
    assert resolved.agent_name == "custom"
    assert resolved.hook_projection_map is hook_projection
    assert resolved.projection_maps == (projection,)

    resolved_default = _resolve_config(
        config=None,
        agent_name=None,
        projection_maps=None,
    )
    assert resolved_default.agent_name == "pydantic-acp"

    seen: dict[str, Any] = {}

    def fake_create_acp_agent(*args, **kwargs):
        del args, kwargs
        return "adapter"

    async def fake_run_agent(adapter):
        seen["adapter"] = adapter

    def fake_asyncio_run(coro):
        asyncio.run(coro)

    monkeypatch.setattr(server_module, "create_acp_agent", fake_create_acp_agent)
    monkeypatch.setattr(server_module, "run_agent", fake_run_agent)
    monkeypatch.setattr(server_module, "asyncio", SimpleNamespace(run=fake_asyncio_run))

    server_module.run_acp(agent=Agent(TestModel()))
    assert seen["adapter"] == "adapter"


def test_bridge_manager_merges_metadata_and_classification_fallbacks(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    agent = Agent(TestModel(custom_output_text="unused"))

    class MetadataOnlyBridge(CapabilityBridge):
        metadata_key = None

        def get_session_metadata(self, session, agent):
            del session, agent
            return cast(Any, {"hidden": True})

    class FullBridge(CapabilityBridge):
        metadata_key = "visible"

        def drain_updates(self, session, agent):
            del session, agent
            return cast(Any, [_LOW_LEVEL_SENTINEL])

        def get_config_options(self, session, agent):
            del session, agent
            return cast(
                Any,
                [
                    SessionConfigOptionBoolean(
                        id="flag",
                        name="Flag",
                        type="boolean",
                        current_value=True,
                    )
                ],
            )

        def get_mcp_capabilities(self, agent=None):
            del agent
            return cast(Any, SimpleNamespace(http=True, sse=False))

        def get_mode_state(self, session, agent):
            del session, agent
            return ModeState(current_mode_id="plan", modes=[SessionMode(id="plan", name="Plan")])

        def get_session_metadata(self, session, agent):
            del session, agent
            return cast(Any, {"ok": True})

        def set_config_option(self, session, agent, config_id, value):
            del session, agent
            if config_id != "flag":
                return None
            return cast(
                Any,
                [
                    SessionConfigOptionBoolean(
                        id="flag",
                        name="Flag",
                        type="boolean",
                        current_value=bool(value),
                    )
                ],
            )

        def set_mode(self, session, agent, mode_id):
            del session, agent
            if mode_id != "plan":
                return None
            return ModeState(current_mode_id="plan", modes=[SessionMode(id="plan", name="Plan")])

        def get_tool_kind(self, tool_name, raw_input=None):
            del raw_input
            return "execute" if tool_name == "bridge-tool" else None

        def get_approval_policy_key(self, tool_name, raw_input=None):
            del raw_input
            return "bridge:key" if tool_name == "bridge-tool" else None

    manager = BridgeManager(
        base_classifier=DefaultToolClassifier(),
        bridges=[MetadataOnlyBridge(), FullBridge()],
    )

    assert manager.drain_updates(session, agent) is not None
    assert manager.get_config_options(session, agent) is not None
    caps = manager.get_mcp_capabilities(agent)
    assert caps.http is True
    assert caps.sse is False
    assert manager.get_mode_state(session, agent) is not None
    assert manager.get_metadata_sections(session, agent) == {"visible": {"ok": True}}
    assert manager.set_config_option(session, agent, "missing", True) is None
    assert manager.set_config_option(session, agent, "flag", False) is not None
    assert manager.set_mode(session, agent, "missing") is None
    assert manager.set_mode(session, agent, "plan") is not None
    assert manager.tool_classifier.classify("bridge-tool") == "execute"
    assert manager.tool_classifier.classify("other-tool") == "execute"
    assert manager.tool_classifier.approval_policy_key("bridge-tool") == "bridge:key"
    assert manager.tool_classifier.approval_policy_key("other-tool") == "other-tool"


def test_projection_helpers_handle_fallback_and_edge_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection = FileSystemProjectionMap(
        default_write_tool="write_file",
        default_read_tool="read_file",
        default_bash_tool="run_command",
    )
    assert compose_projection_maps(None) is None
    assert compose_projection_maps([]) is None
    assert compose_projection_maps([projection]) is projection
    assert isinstance(compose_projection_maps([projection, projection]), CompositeProjectionMap)

    assert projection.project_start("run_command", raw_input=[]) is None
    assert projection.project_start("run_command", raw_input={"cmd": ""}) is None
    assert projection.project_start("write_file", raw_input=[]) is None
    assert projection.project_start("write_file", raw_input={"path": ""}) is None
    assert (
        projection.project_progress(
            "write_file",
            raw_input={"path": "x", "content": "y"},
            raw_output=None,
            serialized_output="",
            status="failed",
        )
        is None
    )
    assert (
        projection.project_progress(
            "read_file",
            raw_input=[],
            raw_output=None,
            serialized_output="",
            status="completed",
        )
        is None
    )
    assert (
        projection.project_progress(
            "read_file",
            raw_input={"path": ""},
            raw_output=None,
            serialized_output="",
            status="completed",
        )
        is None
    )

    assert extract_tool_call_locations([]) is None
    assert extract_tool_call_locations({"other": "x"}) is None
    assert _candidate_keys(None, ("a", "b")) == ("a", "b")
    assert _candidate_keys("x", ("a", "b")) == ("x", "a", "b")
    assert cast(Any, ToolClassifier.classify)(_LOW_LEVEL_SENTINEL, "tool") is None
    assert cast(Any, ToolClassifier.approval_policy_key)(_LOW_LEVEL_SENTINEL, "tool") is None
    assert cast(Any, ProjectionMap.project_start)(_LOW_LEVEL_SENTINEL, "tool") is None
    assert (
        cast(Any, ProjectionMap.project_progress)(
            _LOW_LEVEL_SENTINEL,
            "tool",
            serialized_output="x",
            status="completed",
        )
        is None
    )
    assert _merge_tool_projections([None]) is None
    merged = _merge_tool_projections(
        [
            ToolProjection(title="first"),
            ToolProjection(status="failed"),
        ]
    )
    assert merged is not None
    assert merged.title == "first"
    assert merged.status == "failed"

    known_start = build_tool_start_update(
        ToolCallPart("write_file", {"path": "a", "content": "b"}, tool_call_id="1"),
        classifier=DefaultToolClassifier(),
        projection_map=projection,
        cwd=tmp_path,
    )
    keep_diff = _preserve_file_diff_content(
        known_start=known_start,
        projection=ToolProjection(content=known_start.content),
    )
    assert keep_diff == known_start.content
    assert (
        _preserve_file_diff_content(known_start=None, projection=ToolProjection(content=[])) == []
    )
    assert _preserve_file_diff_content(
        known_start=known_start,
        projection=ToolProjection(
            content=[ContentToolCallContent(type="content", content=text_block("x"))]
        ),
    ) == [ContentToolCallContent(type="content", content=text_block("x"))]
    assert (
        _preserve_file_diff_content(
            known_start=known_start,
            projection=ToolProjection(content=[]),
        )
        == []
    )
    assert (
        _preserve_file_diff_content(
            known_start=ToolCallStart(
                session_update="tool_call",
                tool_call_id="1",
                title="write_file",
                kind="edit",
                status="in_progress",
                content=[ContentToolCallContent(type="content", content=text_block("x"))],
            ),
            projection=ToolProjection(content=known_start.content),
        )
        == known_start.content
    )
    assert projection._terminal_id_from_value("bad") is None
    assert projection._terminal_id_from_value({"terminalId": "abc"}) == "abc"

    assert _stringify_value("x", None) == "x"
    assert _stringify_value(1, "fallback") == "fallback"
    assert _stringify_value(1, None) == "1"
    assert _read_existing_text("relative.txt", cwd=None) == ""
    assert _read_existing_text("missing.txt", cwd=tmp_path) == ""
    bad_file = tmp_path / "bad.txt"
    bad_file.write_text("content", encoding="utf-8")
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))
    assert _read_existing_text(str(bad_file), cwd=tmp_path) == ""

    assert _preview_text("x" * 5000).endswith("...[truncated]")
    assert _single_line_preview("a\nb", limit=10) == "a b"
    assert _single_line_preview("x" * 20, limit=5) == "xxxxx..."
    assert _format_command_title("echo hi").startswith("Execute ")
    assert _bash_status(None, fallback="completed") == "completed"
    assert _bash_status({"timed_out": True}, fallback="completed") == "failed"
    assert _bash_status({"timed_out": 1}, fallback="completed") == "failed"
    assert _bash_status({"returncode": 1}, fallback="completed") == "failed"
    assert DefaultToolClassifier().classify("delete_file") == "delete"
    assert DefaultToolClassifier().classify("move_file") == "move"
    assert DefaultToolClassifier().classify("search_repo") == "search"
    assert DefaultToolClassifier().classify("fetch_url") == "fetch"
    assert DefaultToolClassifier().classify("think_step") == "think"

    content = _bash_progress_content(
        raw_input={"command": "echo hi"},
        raw_output={"returncode": 2, "stdout": "out", "stderr": "err"},
        serialized_output="serialized",
    )
    assert "Status: failed" in content[0].content.text
    assert "Stdout:" in content[0].content.text
    assert "Stderr:" in content[0].content.text
    fallback_content = _bash_progress_content(
        raw_input=None,
        raw_output=None,
        serialized_output="serialized",
    )
    assert fallback_content[0].content.text == "serialized"
    success_content = _bash_progress_content(
        raw_input={"other": "x"},
        raw_output={"returncode": 0},
        serialized_output="serialized",
    )
    assert "Status: success" in success_content[0].content.text

    retry = RetryPromptPart(
        tool_name="write_file",
        tool_call_id="1",
        content="try again",
    )
    progress = build_tool_progress_update(
        retry,
        classifier=DefaultToolClassifier(),
        known_start=known_start,
        projection_map=projection,
        serializer=DefaultOutputSerializer(),
    )
    assert progress.status == "failed"

    duplicate_updates = build_tool_updates(
        [
            ModelResponse(
                parts=[ToolCallPart("write_file", {"path": "a", "content": "b"}, tool_call_id="1")]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart("final_result", "done"),
                    RetryPromptPart("retry", tool_name=None),
                ]
            ),
        ],
        classifier=DefaultToolClassifier(),
        known_starts={"1": known_start},
        projection_map=projection,
        serializer=DefaultOutputSerializer(),
    )
    assert duplicate_updates == []


def test_native_approval_bridge_handles_remembered_policies_and_invalid_options(
    tmp_path: Path,
) -> None:
    session = _session(tmp_path)
    tool_call = ToolCallPart("write_file", {"path": "demo.txt", "content": "x"}, tool_call_id="1")
    requests = DeferredToolRequests(calls=[], approvals=[tool_call], metadata={})
    classifier = DefaultToolClassifier()
    bridge = NativeApprovalBridge(enable_persistent_choices=True)

    session.metadata["approval_policies"] = {"write_file": "allow"}
    resolution = asyncio.run(
        bridge.resolve_deferred_approvals(
            client=RecordingClient(),
            session=session,
            requests=requests,
            classifier=classifier,
        )
    )
    assert isinstance(resolution.deferred_tool_results.approvals["1"], ToolApproved)
    session.metadata["approval_policies"] = {"write_file": "reject"}
    rejected = asyncio.run(
        bridge.resolve_deferred_approvals(
            client=RecordingClient(),
            session=session,
            requests=requests,
            classifier=classifier,
        )
    )
    assert isinstance(rejected.deferred_tool_results.approvals["1"], ToolDenied)

    class InvalidOptionClient(RecordingClient):
        async def request_permission(self, *args, **kwargs):
            del args, kwargs
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id="bad-option")
            )

    session.metadata["approval_policies"] = "bad"
    with pytest.raises(RequestError):
        asyncio.run(
            bridge.resolve_deferred_approvals(
                client=InvalidOptionClient(),
                session=session,
                requests=requests,
                classifier=classifier,
            )
        )

    disabled = NativeApprovalBridge(enable_persistent_choices=False)
    disabled._remember_policy(
        session=session,
        approval_policy_key="write_file",
        option_id="allow_always",
    )
    assert disabled._approval_policies(session) == {}
    assert bridge._selected_option_to_result("unknown") is None
    assert isinstance(bridge._policy_to_result("allow"), ToolApproved)
    assert isinstance(bridge._policy_to_result("reject"), ToolDenied)
    bridge._remember_policy(
        session=session,
        approval_policy_key="write_file",
        option_id="reject_always",
    )
    assert bridge._get_remembered_policy(session, "write_file") == "reject"


def test_build_tool_progress_update_uses_projected_title_without_known_start() -> None:
    @dataclass(slots=True)
    class TitleOnlyProjectionMap:
        title: str

        def project_start(
            self,
            tool_name: str,
            *,
            cwd: Path | None = None,
            raw_input: Any = None,
        ) -> ToolProjection | None:
            del tool_name, cwd, raw_input
            return None

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
            return ToolProjection(title=self.title)

    progress = build_tool_progress_update(
        ToolReturnPart("read_file", "payload", tool_call_id="read-1"),
        classifier=DefaultToolClassifier(),
        known_start=None,
        projection_map=TitleOnlyProjectionMap(title="Projected Read"),
        serializer=DefaultOutputSerializer(),
    )

    assert progress.title == "Projected Read"
    assert progress.locations is None
    assert progress.raw_output == "payload"


def test_build_tool_updates_filters_output_parts_and_empty_messages() -> None:
    classifier = DefaultToolClassifier()
    serializer = DefaultOutputSerializer()

    assert (
        build_tool_updates(
            [],
            classifier=classifier,
            projection_map=None,
            serializer=serializer,
        )
        == []
    )

    updates = build_tool_updates(
        [
            ModelResponse(
                parts=[ToolCallPart("final_result", {"value": "done"}, tool_call_id="final-1")]
            ),
            ModelRequest(parts=[ToolReturnPart("final_result", "done")]),
        ],
        classifier=classifier,
        projection_map=None,
        serializer=serializer,
    )

    assert updates == []
