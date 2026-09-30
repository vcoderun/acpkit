from __future__ import annotations as _annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from acp.exceptions import RequestError
from acp.schema import (
    AcceptElicitationResponse,
    ClientCapabilities,
    ElicitationCapabilities,
    ElicitationFormCapabilities,
    ElicitationFormSessionMode,
    ElicitationSchema,
    ElicitationUrlCapabilities,
    ElicitationUrlSessionMode,
    McpServerStdio,
    SessionInfoUpdate,
)
from pydantic import AnyUrl
from pydantic_acp.approvals import ApprovalResolution
from pydantic_acp.bridges.base import CapabilityBridge
from pydantic_acp.runtime._agent_state import (
    clear_selected_model_id,
    set_selected_model_id,
)
from pydantic_acp.runtime._session_runtime import (
    _default_available_models,
    _known_codex_model_ids,
    _known_pydantic_model_ids,
)
from pydantic_acp.runtime.session_surface import SessionSurface
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults

from .support import (
    UTC,
    AcpSessionContext,
    AdapterConfig,
    AdapterModel,
    Agent,
    MemorySessionStore,
    ModelSelectionState,
    Path,
    PrepareToolsBridge,
    PrepareToolsMode,
    TestModel,
    create_acp_agent,
    datetime,
)


def test_list_sessions_filters_by_cwd_and_close_session_handles_missing(
    tmp_path: Path,
) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="ok")),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    adapter_any = cast("Any", adapter)
    first = asyncio.run(adapter.new_session(cwd=str(tmp_path / "a"), mcp_servers=[]))
    second = asyncio.run(adapter.new_session(cwd=str(tmp_path / "b"), mcp_servers=[]))

    filtered = asyncio.run(adapter.list_sessions(cwd=str(tmp_path / "a")))

    assert [session.session_id for session in filtered.sessions] == [first.session_id]
    assert asyncio.run(adapter_any._session_runtime.close_session("missing")) is False
    assert asyncio.run(adapter_any._session_runtime.close_session(second.session_id)) is True
    assert asyncio.run(adapter_any._session_runtime.close_session(second.session_id)) is False


def test_session_context_forwards_supported_form_elicitation() -> None:
    class ElicitationClient:
        def __init__(self) -> None:
            self.create_calls: list[tuple[str, Any]] = []
            self.completed_ids: list[str] = []

        async def create_elicitation(self, message: str, mode: Any) -> AcceptElicitationResponse:
            self.create_calls.append((message, mode))
            return AcceptElicitationResponse(action="accept", content={"answer": "yes"})

        async def complete_elicitation(self, elicitation_id: str) -> None:
            self.completed_ids.append(elicitation_id)

    client = ElicitationClient()
    session = AcpSessionContext(
        session_id="elicitation-session",
        cwd=Path("/tmp/acpkit-elicitation"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        client=cast("Any", client),
        client_capabilities=ClientCapabilities(
            elicitation=ElicitationCapabilities(form=ElicitationFormCapabilities()),
        ),
    )
    mode = ElicitationFormSessionMode(
        session_id=session.session_id,
        requested_schema=ElicitationSchema(),
    )

    response = asyncio.run(session.create_elicitation("Confirm execution", mode))
    asyncio.run(session.complete_elicitation("elicitation-1"))

    assert response.action == "accept"
    assert session.supports_elicitation(mode) is True
    assert client.create_calls == [("Confirm execution", mode)]
    assert client.completed_ids == ["elicitation-1"]

    session.client_capabilities = ClientCapabilities()
    with pytest.raises(RequestError):
        asyncio.run(session.create_elicitation("Confirm execution", mode))

    url_mode = ElicitationUrlSessionMode(
        session_id=session.session_id,
        elicitation_id="url-elicitation",
        url=AnyUrl("https://example.com/confirm"),
    )
    session.client_capabilities = ClientCapabilities(
        elicitation=ElicitationCapabilities(url=ElicitationUrlCapabilities()),
    )
    assert session.supports_elicitation(url_mode) is True
    assert session.supports_elicitation(cast("Any", object())) is False

    session.client = None
    with pytest.raises(RequestError):
        asyncio.run(session.create_elicitation("Confirm execution", url_mode))
    with pytest.raises(RequestError):
        asyncio.run(session.complete_elicitation("elicitation-1"))


def test_session_context_requires_runtime_callbacks() -> None:
    session = AcpSessionContext(
        session_id="detached-session",
        cwd=Path("/tmp/acpkit-detached-session"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    with pytest.raises(RequestError):
        asyncio.run(
            session.emit_update(
                SessionInfoUpdate(
                    session_update="session_info_update",
                    title="Detached",
                    updated_at=datetime.now(UTC).isoformat(),
                ),
            ),
        )
    with pytest.raises(RequestError):
        asyncio.run(session.resolve_deferred_approvals(DeferredToolRequests()))


@pytest.mark.asyncio
async def test_close_session_rejects_self_close(tmp_path: Path) -> None:
    store = MemorySessionStore()
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="ok")),
        config=AdapterConfig(session_store=store),
    )
    session = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
    adapter_any = cast("Any", adapter)
    current_task = asyncio.current_task()
    assert current_task is not None
    adapter_any._active_prompt_tasks[session.session_id] = current_task

    try:
        with pytest.raises(RuntimeError, match="cannot close its own session"):
            await adapter.close_session(session_id=session.session_id)
    finally:
        adapter_any._active_prompt_tasks.pop(session.session_id, None)

    assert store.get(session.session_id) is not None
    assert session.session_id not in adapter_any._closing_sessions


@pytest.mark.asyncio
async def test_source_approval_resolution_records_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemorySessionStore()
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="ok")),
        config=AdapterConfig(session_store=store),
    )
    response = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
    adapter_any = cast("Any", adapter)
    session = adapter_any._session_runtime._require_session(response.session_id)
    tool_call = ToolCallPart("write_file", {"path": "blocked.txt"}, tool_call_id="call-1")
    expected = ApprovalResolution(
        deferred_tool_results=DeferredToolResults(),
        cancelled=True,
        cancelled_tool_call=tool_call,
    )
    recorded: list[ToolCallPart | None] = []

    async def resolve_deferred_approvals(
        *,
        session: AcpSessionContext,
        requests: DeferredToolRequests,
    ) -> ApprovalResolution:
        del session, requests
        return expected

    async def record_cancelled_approval(
        session: AcpSessionContext,
        cancelled_tool_call: ToolCallPart | None,
    ) -> None:
        del session
        recorded.append(cancelled_tool_call)

    monkeypatch.setattr(adapter_any, "_resolve_deferred_approvals", resolve_deferred_approvals)
    monkeypatch.setattr(adapter_any, "_record_cancelled_approval", record_cancelled_approval)

    result = await adapter_any._session_runtime._resolve_source_approvals(
        session,
        DeferredToolRequests(approvals=[tool_call]),
    )

    assert result is expected
    assert recorded == [tool_call]
    persisted = store.get(response.session_id)
    assert persisted is not None
    assert persisted.updated_at == session.updated_at


def test_prompt_lock_rejects_locked_lock_from_another_event_loop() -> None:
    adapter = create_acp_agent(agent=Agent(TestModel(custom_output_text="ok")))
    adapter_any = cast("Any", adapter)

    async def create_locked_lock() -> tuple[asyncio.AbstractEventLoop, asyncio.Lock]:
        lock = asyncio.Lock()
        await lock.acquire()
        return asyncio.get_running_loop(), lock

    owner_loop, lock = asyncio.run(create_locked_lock())
    adapter_any._prompt_locks["cross-loop"] = (owner_loop, lock)

    async def get_prompt_lock() -> None:
        adapter_any._prompt_lock("cross-loop")

    try:
        with pytest.raises(RuntimeError, match="cannot move between active event loops"):
            asyncio.run(get_prompt_lock())
    finally:
        lock.release()


def test_session_runtime_rejects_invalid_model_and_mode_config_types(
    tmp_path: Path,
) -> None:
    agent = Agent(TestModel(custom_output_text="ok"))

    def keep_tools(_ctx: Any, tool_defs: list[Any]) -> list[Any]:
        return list(tool_defs)  # pragma: no cover

    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(
            available_models=[
                AdapterModel(
                    model_id="test:model",
                    name="Test Model",
                    override=cast("Any", agent.model),
                ),
            ],
            capability_bridges=[
                PrepareToolsBridge(
                    default_mode_id="chat",
                    modes=[PrepareToolsMode(id="chat", name="Chat", prepare_func=keep_tools)],
                ),
            ],
            session_store=MemorySessionStore(),
        ),
    )
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    with pytest.raises(RequestError):
        asyncio.run(adapter.set_config_option("model", session.session_id, True))
    with pytest.raises(RequestError):
        asyncio.run(adapter.set_config_option("mode", session.session_id, True))


def test_session_runtime_rejects_relative_additional_directories() -> None:
    adapter = create_acp_agent(agent=Agent(TestModel(custom_output_text="ok")))
    runtime = cast("Any", adapter)._session_runtime

    assert runtime._normalize_additional_directories(["/tmp"]) == (Path("/tmp").resolve(),)

    with pytest.raises(RequestError):
        runtime._normalize_additional_directories(["relative"])


def test_session_runtime_helper_inventory_and_missing_session_paths(
    tmp_path: Path,
) -> None:
    current_model = "openrouter:google/gemini-3-flash-preview"
    available_models = _default_available_models(
        current_model,
        current_model_value=current_model,
    )
    model_ids = [model.model_id for model in available_models]

    assert model_ids[0] == current_model
    assert len(model_ids) == len(set(model_ids))
    assert set(_known_codex_model_ids()) == {
        "codex:gpt-5.4",
        "codex:gpt-5.4-mini",
        "codex:gpt-5.3-codex",
        "codex:gpt-5.2",
    }
    assert "test" not in _known_pydantic_model_ids()

    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="ok")),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    adapter_any = cast("Any", adapter)
    stored_session = adapter_any._config.session_store.get(session.session_id)
    assert stored_session is not None
    adapter_any._update_session_mcp_servers(stored_session, [])
    assert stored_session.mcp_servers == []

    with pytest.raises(RequestError):
        adapter_any._session_runtime._require_session("missing")


def test_set_session_model_covers_missing_state_and_unconfigured_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="ok")),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    adapter_any = cast("Any", adapter)
    runtime = adapter_any._session_runtime
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    async def no_model_state(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(runtime, "_get_model_selection_state", no_model_state)
    assert asyncio.run(adapter.set_session_model("missing", session.session_id)) is None

    async def fixed_model_state(*_args: Any, **_kwargs: Any) -> ModelSelectionState:
        return ModelSelectionState(
            available_models=[],
            current_model_id="configured-model",
            allow_any_model_id=False,
        )

    monkeypatch.setattr(runtime, "_get_model_selection_state", fixed_model_state)
    with pytest.raises(RequestError):
        asyncio.run(adapter.set_session_model("missing", session.session_id))

    async def permissive_model_state(*_args: Any, **_kwargs: Any) -> ModelSelectionState:
        return ModelSelectionState(
            available_models=[],
            current_model_id="configured-model",
            allow_any_model_id=True,
        )

    async def fake_build_surface(*_args: Any, **_kwargs: Any) -> SessionSurface:
        return SessionSurface(
            config_options=[],
            mode_state=None,
            plan_entries=None,
        )

    async def fake_emit_updates(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(runtime, "_get_model_selection_state", permissive_model_state)
    monkeypatch.setattr(
        runtime,
        "_resolve_unconfigured_model_id",
        lambda model_id: model_id.strip(),
    )
    monkeypatch.setattr(runtime, "_remember_default_model", lambda _agent: None)
    monkeypatch.setattr(runtime, "_build_session_surface", fake_build_surface)
    monkeypatch.setattr(runtime, "_emit_session_state_updates", fake_emit_updates)

    response = asyncio.run(adapter.set_session_model(" custom:model ", session.session_id))
    stored_session = adapter_any._config.session_store.get(session.session_id)

    assert response is not None
    assert stored_session is not None
    assert stored_session.session_model_id == "custom:model"
    assert stored_session.config_values["model"] == "custom:model"


def test_set_config_option_decline_and_bridge_runtime_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BridgeConfigOptionHandler(CapabilityBridge):
        def set_config_option(
            self,
            session: Any,
            agent: Any,
            config_id: str,
            value: str | bool,
        ) -> list[Any] | None:
            del session, agent
            if config_id == "custom" and value is True:
                return []
            return None

    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="ok")),
        config=AdapterConfig(
            session_store=MemorySessionStore(),
            capability_bridges=[cast("Any", BridgeConfigOptionHandler())],
        ),
    )
    adapter_any = cast("Any", adapter)
    runtime = adapter_any._session_runtime
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    async def declined_model(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def declined_mode(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(runtime, "set_session_model", declined_model)
    monkeypatch.setattr(runtime, "set_session_mode", declined_mode)
    assert asyncio.run(adapter.set_config_option("model", session.session_id, "x")) is None
    assert asyncio.run(adapter.set_config_option("mode", session.session_id, "chat")) is None

    async def fake_build_surface(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(config_options=[])

    async def fake_emit_updates(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(runtime, "_build_session_surface", fake_build_surface)
    monkeypatch.setattr(runtime, "_emit_session_state_updates", fake_emit_updates)

    response = asyncio.run(adapter.set_config_option("custom", session.session_id, True))
    assert response is not None
    assert response.config_options == []


def test_session_runtime_misc_runtime_helpers_and_serialization(
    tmp_path: Path,
) -> None:
    agent = Agent(TestModel(custom_output_text="ok"))
    adapter = create_acp_agent(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    adapter_any = cast("Any", adapter)
    runtime = adapter_any._session_runtime
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    stored_session = adapter_any._config.session_store.get(session.session_id)

    assert stored_session is not None
    assert runtime._supports_fallback_model_selection() is True
    assert runtime._model_identity("openrouter:model") == "openrouter:model"
    assert runtime._model_identity(SimpleNamespace(model_name="named-model")) == "named-model"
    assert runtime._model_identity(SimpleNamespace(model_name=1)) is None

    with pytest.raises(RequestError):
        runtime._require_model_option("missing")

    stored_session.session_model_id = "session-model"
    assert runtime._resolve_current_model_id(stored_session, agent) == "session-model"
    stored_session.session_model_id = None
    set_selected_model_id(agent, "selected-model")
    assert runtime._resolve_current_model_id(stored_session, agent) == "selected-model"
    clear_selected_model_id(agent)
    agent.model = "fallback-model"
    assert runtime._resolve_current_model_id(stored_session, agent) == "fallback-model"

    original_mcp_servers = list(stored_session.mcp_servers)
    runtime._update_session_mcp_servers(stored_session, None)
    assert stored_session.mcp_servers == original_mcp_servers
    runtime._update_session_mcp_servers(
        stored_session,
        [McpServerStdio(name="repo", command="python", args=["-m", "server"], env=[])],
    )
    assert stored_session.mcp_servers == [
        {
            "args": ["-m", "server"],
            "command": "python",
            "env": {},
            "name": "repo",
            "transport": "stdio",
        },
    ]

    current_model = " "
    available_models = _default_available_models(current_model, current_model_value=current_model)
    assert available_models[0].model_id != ""
    assert (
        asyncio.run(
            runtime._build_config_options(
                stored_session,
                agent,
                model_selection_state=None,
                mode_state=None,
            ),
        )
        is None
    )
    assert asyncio.run(runtime._get_plan_entries(stored_session, agent)) is None
    assert asyncio.run(runtime._synchronize_session_metadata(stored_session, agent)) is None
    assert asyncio.run(runtime._get_approval_state(stored_session, agent)) is None
    runtime._validate_mode_state(None)
    runtime._synchronize_mode_state(stored_session, None)
    assert runtime._plan_storage_metadata(stored_session) is None
