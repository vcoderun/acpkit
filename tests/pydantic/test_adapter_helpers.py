from __future__ import annotations as _annotations

import asyncio
import builtins
import importlib
from types import SimpleNamespace
from typing import Any, cast

import pytest
from acp.exceptions import RequestError
from acp.schema import PromptResponse
from pydantic_acp.runtime import _native_plan_runtime as native_plan_runtime_module
from pydantic_acp.runtime import _prompt_execution as prompt_execution_module
from pydantic_acp.runtime._agent_state import (
    assign_model,
    clear_selected_model_id,
    default_model,
    has_native_plan_tools,
    remember_default_model,
    selected_model_id,
    set_active_session,
    set_native_plan_tools_installed,
    set_selected_model_id,
    try_active_session,
)
from pydantic_acp.runtime.adapter import NativePlanGeneration, TaskPlan
from pydantic_acp.runtime.prompts import load_message_history
from pydantic_acp.runtime.session_surface import SessionSurface
from pydantic_acp.types import (
    HttpMcpServer,
    ImageContentBlock,
    McpServerStdio,
    PlanEntry,
    SseMcpServer,
)
from pydantic_ai import models as pydantic_models
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.tools import DeferredToolRequests
from typing_extensions import Sentinel

from examples.pydantic.travel_agent import TravelPromptModelProvider

from .support import (
    UTC,
    AcpSessionContext,
    AdapterConfig,
    AdapterModel,
    Agent,
    ConfigOption,
    MemorySessionStore,
    ModelSelectionState,
    ModeState,
    OpenAICompactionBridge,
    Path,
    PrepareToolsBridge,
    PrepareToolsMode,
    RecordingClient,
    SessionConfigOptionBoolean,
    SessionMode,
    TestModel,
    agent_message_texts,
    create_acp_agent,
    datetime,
    text_block,
)

_INVALID_TEST_VALUE = Sentinel("_INVALID_TEST_VALUE")


def _adapter(*, agent: Agent[Any, Any], config: AdapterConfig | None = None) -> Any:
    return create_acp_agent(
        agent=agent,
        config=config or AdapterConfig(session_store=MemorySessionStore()),
    )


def _stored_session(adapter: Any, session_id: str) -> Any:
    return adapter._config.session_store.get(session_id)


def _fake_run_result(output: Any) -> Any:
    return SimpleNamespace(
        output=output,
        new_messages=lambda: [],
        all_messages=lambda: [],
        all_messages_json=lambda: b"[]",
    )


class _MutableAgent:
    def __init__(self) -> None:
        self.model: str = "initial-model"
        self._acp_selected_model_id: str | int | None = None
        self._acp_active_session: AcpSessionContext | str | None = None
        self._acp_default_model: str | None = None
        self._acp_native_plan_tools_installed: bool = False


def test_session_model_selection_respects_provider_and_fallback_state(
    tmp_path: Path,
) -> None:
    agent = Agent(TestModel(custom_output_text="ok"))
    adapter = _adapter(agent=agent, config=AdapterConfig(session_store=MemorySessionStore()))
    response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    session = _stored_session(adapter, response.session_id)

    cast(Any, agent).model = _INVALID_TEST_VALUE
    session.session_model_id = None
    model_state = asyncio.run(adapter._get_model_selection_state(session, agent))
    assert model_state is None

    session.session_model_id = "session-model"
    model_state = asyncio.run(adapter._get_model_selection_state(session, agent))
    assert model_state is not None
    assert model_state.current_model_id == "session-model"
    assert model_state.available_models[0].model_id == "session-model"

    selected_agent = Agent(TestModel(custom_output_text="selected"))
    set_selected_model_id(selected_agent, "selected-model")
    selected_adapter = _adapter(
        agent=selected_agent,
        config=AdapterConfig(
            session_store=MemorySessionStore(),
            available_models=[
                AdapterModel(
                    model_id="model-a",
                    name="Model A",
                    override=cast(Any, selected_agent.model),
                )
            ],
        ),
    )
    session.session_model_id = None
    assert selected_adapter._resolve_current_model_id(session, selected_agent) == "selected-model"
    assert selected_adapter._resolve_model_id_from_value(selected_agent.model) == "model-a"
    assert selected_adapter._resolve_model_id_from_value("plain-model") == "plain-model"
    assert selected_adapter._resolve_model_id_from_value(cast(Any, _INVALID_TEST_VALUE)) is None

    class DemoModelsProvider:
        def set_model(self, session, agent, model_id):
            del session, agent, model_id
            return None

        def get_model_state(self, session, agent):
            del session, agent
            return ModelSelectionState(
                current_model_id=None,
                available_models=[],
            )

    provider_adapter = _adapter(
        agent=Agent(TestModel(custom_output_text="provider")),
        config=AdapterConfig(
            models_provider=DemoModelsProvider(),
            session_store=MemorySessionStore(),
        ),
    )
    provider_response = asyncio.run(provider_adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    provider_session = _stored_session(provider_adapter, provider_response.session_id)
    provider_session.config_values["model"] = "stale"
    set_response = asyncio.run(
        provider_adapter.set_session_model("provider-model", provider_response.session_id)
    )
    assert set_response is not None
    assert provider_session.session_model_id is None
    provider_state = asyncio.run(
        provider_adapter._get_model_selection_state(provider_session, agent)
    )
    assert provider_state is not None
    assert provider_state.current_model_id is None

    missing_response = asyncio.run(
        provider_adapter.set_session_model("missing", provider_response.session_id)
    )
    assert missing_response is not None
    assert "model" not in provider_session.config_values


def test_session_mode_and_config_updates_flow_through_providers(tmp_path: Path) -> None:
    class DemoModesProvider:
        def set_mode(self, session, agent, mode_id):
            del session, agent, mode_id
            return None

        def get_mode_state(self, session, agent):
            del session, agent
            return ModeState(
                current_mode_id="review",
                modes=[SessionMode(id="review", name="Review")],
            )

    class EmptyConfigProvider:
        def set_config_option(self, session, agent, config_id, value):
            del session, agent, config_id, value
            return None

        def get_config_options(self, session, agent):
            del session, agent
            return None

    class MatchingConfigProvider:
        def set_config_option(self, session, agent, config_id, value):
            del session, agent, config_id, value
            return None

        def get_config_options(self, session, agent):
            del session, agent
            return cast(
                Any,
                [
                    SessionConfigOptionBoolean(
                        id="flag",
                        name="Flag",
                        type="boolean",
                        current_value=False,
                    )
                ],
            )

    mode_agent = Agent(TestModel(custom_output_text="mode"))
    mode_adapter = _adapter(
        agent=mode_agent,
        config=AdapterConfig(
            modes_provider=DemoModesProvider(),
            session_store=MemorySessionStore(),
        ),
    )
    mode_response = asyncio.run(mode_adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    mode_session = _stored_session(mode_adapter, mode_response.session_id)
    mode_state = asyncio.run(mode_adapter.set_session_mode("review", mode_response.session_id))
    assert mode_state is not None
    assert mode_session.config_values["mode"] == "review"

    assert asyncio.run(mode_adapter.set_config_option("x", mode_response.session_id, True)) is None

    empty_adapter = _adapter(
        agent=Agent(TestModel()),
        config=AdapterConfig(
            config_options_provider=EmptyConfigProvider(),
            session_store=MemorySessionStore(),
        ),
    )
    empty_response = asyncio.run(empty_adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    assert (
        asyncio.run(empty_adapter.set_config_option("flag", empty_response.session_id, True))
        is None
    )

    matching_adapter = _adapter(
        agent=Agent(TestModel()),
        config=AdapterConfig(
            config_options_provider=MatchingConfigProvider(),
            session_store=MemorySessionStore(),
        ),
    )
    matching_response = asyncio.run(matching_adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    config_response = asyncio.run(
        matching_adapter.set_config_option("flag", matching_response.session_id, True)
    )
    assert config_response is not None


def test_native_plan_state_syncs_through_runtime_outputs(tmp_path: Path) -> None:
    def pass_through(ctx, tool_defs):
        del ctx  # pragma: no cover
        return list(tool_defs)  # pragma: no cover

    persisted_states: list[tuple[list[str], str | None]] = []

    class RecordingPlanPersistenceProvider:
        def persist_plan_state(self, session, agent, entries, plan_markdown):
            del session, agent
            persisted_states.append(([entry.content for entry in entries], plan_markdown))

    plan_bridge = PrepareToolsBridge(
        default_mode_id="plan",
        modes=[
            PrepareToolsMode(
                id="plan",
                name="Plan",
                prepare_func=pass_through,
                plan_mode=True,
            )
        ],
    )
    agent = Agent(TestModel(custom_output_text="plan"), output_type=str)
    adapter = _adapter(
        agent=agent,
        config=AdapterConfig(
            capability_bridges=[plan_bridge],
            native_plan_persistence_provider=RecordingPlanPersistenceProvider(),
            session_store=MemorySessionStore(),
        ),
    )
    response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    session = _stored_session(adapter, response.session_id)

    output_type = adapter._build_run_output_type(agent, session=session)
    assert output_type is not None
    assert output_type == [TaskPlan, DeferredToolRequests]
    assert adapter._contains_text_output([int, str]) is True
    assert adapter._contains_text_output([int, float]) is False
    assert adapter._contains_native_plan_generation([str, NativePlanGeneration]) is True

    native_output = NativePlanGeneration(
        plan_md="# Plan",
        plan_entries=[PlanEntry(content="Write the plan", priority="high", status="in_progress")],
    )
    assert (
        adapter._synchronize_native_plan_output(session, native_output, streamed_output=True) == ""
    )
    asyncio.run(adapter._persist_current_native_plan_state(session, agent=agent))
    assert session.plan_markdown == "# Plan"
    assert persisted_states == [(["Write the plan"], "# Plan")]
    assert adapter._format_native_plan(session) == (
        "# Plan\n\nCurrent plan entries:\n\n1. [in_progress] (high) Write the plan\n\n"
        "Use these 1-based entry numbers with `acp_update_plan_entry` and "
        "`acp_mark_plan_done`."
    )

    session.plan_markdown = None
    session.plan_entries = []
    assert adapter._format_native_plan(session) == "No plan has been recorded yet."

    session.plan_entries = [
        PlanEntry(content="Inspect", priority="medium", status="pending").model_dump(mode="json")
    ]
    assert "Inspect" in adapter._format_native_plan(session)

    adapter._install_native_plan_tools(agent)
    tools = agent._function_toolset.tools
    get_plan_tool = cast(Any, tools["acp_get_plan"])
    set_plan_tool = cast(Any, tools["acp_set_plan"])
    assert get_plan_tool.function() == "No plan has been recorded yet."
    assert persisted_states[-1] == (["Write the plan"], "# Plan")
    assert get_plan_tool.prepare(None, get_plan_tool.function_schema) is not None
    assert set_plan_tool.prepare(None, set_plan_tool.function_schema) is None


def test_tool_based_plan_generation_keeps_agent_output_type_and_exposes_set_plan(
    tmp_path: Path,
) -> None:
    def pass_through(ctx, tool_defs):
        del ctx  # pragma: no cover
        return list(tool_defs)  # pragma: no cover

    plan_bridge = PrepareToolsBridge(
        default_mode_id="plan",
        modes=[
            PrepareToolsMode(
                id="plan",
                name="Plan",
                prepare_func=pass_through,
                plan_mode=True,
            )
        ],
    )
    agent = Agent(TestModel(custom_output_text="plan"), output_type=str)
    adapter = _adapter(
        agent=agent,
        config=AdapterConfig(
            capability_bridges=[plan_bridge],
            session_store=MemorySessionStore(),
        ),
    )
    response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    session = _stored_session(adapter, response.session_id)

    plan_bridge.set_config_option(
        session,
        agent,
        "plan_generation_type",
        "tools",
    )
    assert adapter._build_run_output_type(agent, session=session) == [
        str,
        DeferredToolRequests,
    ]

    adapter._install_native_plan_tools(agent)
    set_active_session(agent, session)
    tools = agent._function_toolset.tools
    set_plan_tool = cast(Any, tools["acp_set_plan"])
    assert set_plan_tool.prepare(None, set_plan_tool.function_schema) is not None


def test_native_plan_runtime_disables_progress_and_writes_without_plan_bridge(
    tmp_path: Path,
) -> None:
    adapter = _adapter(
        agent=Agent(TestModel(custom_output_text="plain")),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    session = _stored_session(adapter, response.session_id)

    native_plan_runtime = adapter._prompt_runtime._native_plan_runtime
    assert native_plan_runtime.supports_native_plan_progress(session) is False
    assert native_plan_runtime.supports_native_plan_writes(session) is False


def test_adapter_runtime_mixins_delegate_prompt_and_session_helpers(
    tmp_path: Path,
) -> None:
    class DemoModelsProvider:
        def get_model_state(self, session, agent):
            del session, agent
            return ModelSelectionState(
                current_model_id="demo-model",
                available_models=[
                    AdapterModel(
                        model_id="demo-model",
                        name="Demo Model",
                        override=TestModel(custom_output_text="demo"),
                    )
                ],
            )

        def set_model(self, session, agent, model_id):
            del session, agent
            return ModelSelectionState(
                current_model_id=model_id,
                available_models=[
                    AdapterModel(
                        model_id=model_id,
                        name="Configured Model",
                        override=TestModel(custom_output_text="configured"),
                    )
                ],
            )

    class DemoModesProvider:
        def get_mode_state(self, session, agent):
            del session, agent
            return ModeState(
                current_mode_id="plan",
                modes=[SessionMode(id="plan", name="Plan")],
            )

        def set_mode(self, session, agent, mode_id):
            del session, agent
            return ModeState(
                current_mode_id=mode_id,
                modes=[SessionMode(id=mode_id, name=mode_id.title())],
            )

    class DemoConfigProvider:
        def get_config_options(self, session, agent) -> list[ConfigOption]:
            del session, agent
            return [
                SessionConfigOptionBoolean(
                    id="flag",
                    name="Flag",
                    type="boolean",
                    current_value=False,
                )
            ]

        def set_config_option(
            self,
            session,
            agent,
            config_id,
            value,
        ) -> list[ConfigOption] | None:
            del session, agent
            if config_id != "flag" or not isinstance(value, bool):
                return None  # pragma: no cover
            return [
                SessionConfigOptionBoolean(
                    id="flag",
                    name="Flag",
                    type="boolean",
                    current_value=value,
                )
            ]

    class PromptOverrideProvider:
        def get_prompt_model_override(
            self,
            session,
            agent,
            prompt,
            model_override,
        ):
            del session, agent, prompt, model_override
            return None

    agent = Agent(TestModel(custom_output_text="ok"))
    adapter = _adapter(
        agent=agent,
        config=AdapterConfig(
            models_provider=DemoModelsProvider(),
            modes_provider=DemoModesProvider(),
            config_options_provider=DemoConfigProvider(),
            prompt_model_override_provider=PromptOverrideProvider(),
            session_store=MemorySessionStore(),
        ),
    )
    response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    session = _stored_session(adapter, response.session_id)

    assert (
        asyncio.run(
            adapter._resolve_prompt_model_override(
                session,
                agent,
                prompt=[text_block("hi")],
                model_override=None,
            )
        )
        is None
    )
    assert asyncio.run(adapter._record_bridge_updates(session, agent)) is None

    model_state = asyncio.run(adapter._set_provider_model_state(session, agent, "override-model"))
    assert model_state is not None
    adapter._synchronize_session_model_selection(session, model_state)

    mode_state = asyncio.run(adapter._set_provider_mode_state(session, agent, "review"))
    assert mode_state is not None
    adapter._synchronize_mode_state(session, mode_state)

    assert asyncio.run(adapter._set_provider_config_options(session, agent, "flag", True)) is True
    assert (
        asyncio.run(
            adapter.resume_session(
                cwd=str(tmp_path),
                session_id=response.session_id,
                mcp_servers=[],
            )
        ).modes
        is not None
    )


def test_agent_state_supports_legacy_attrs_and_runtime_storage(tmp_path: Path) -> None:
    session = AcpSessionContext(
        session_id="session-1",
        cwd=tmp_path,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    legacy_agent = _MutableAgent()
    legacy_agent._acp_active_session = "invalid-session"
    legacy_agent._acp_default_model = "legacy-default"
    legacy_agent._acp_selected_model_id = 123
    legacy_agent._acp_native_plan_tools_installed = True

    assert try_active_session(legacy_agent) is None
    assert default_model(legacy_agent) == "legacy-default"
    assert selected_model_id(legacy_agent) is None
    assert has_native_plan_tools(legacy_agent) is True

    set_selected_model_id(legacy_agent, "model-a")
    assert selected_model_id(legacy_agent) == "model-a"
    clear_selected_model_id(legacy_agent)
    assert legacy_agent._acp_selected_model_id is None

    plain_agent = _MutableAgent()
    del plain_agent._acp_active_session
    del plain_agent._acp_default_model
    del plain_agent._acp_native_plan_tools_installed
    del plain_agent._acp_selected_model_id
    assert try_active_session(plain_agent) is None
    assert default_model(plain_agent) is None
    assert selected_model_id(plain_agent) is None
    assert has_native_plan_tools(plain_agent) is False

    set_active_session(plain_agent, session)
    remember_default_model(plain_agent)
    set_native_plan_tools_installed(plain_agent)
    assign_model(plain_agent, "switched-model")

    assert try_active_session(plain_agent) is session
    assert default_model(plain_agent) == "initial-model"
    assert has_native_plan_tools(plain_agent) is True
    assert plain_agent.model == "switched-model"


def test_runtime_model_restore_and_error_paths_are_handled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = Agent(TestModel(custom_output_text="default"))
    adapter = _adapter(
        agent=agent,
        config=AdapterConfig(
            session_store=MemorySessionStore(),
            approval_bridge=cast(Any, _INVALID_TEST_VALUE),
        ),
    )
    response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    session = _stored_session(adapter, response.session_id)

    assert adapter._restore_default_model(agent, session) is False
    assert adapter._restore_agent_default_model(agent) is False

    session.session_model_id = "model-a"
    assert adapter._restore_default_model(agent, session) is False

    adapter._remember_default_model(agent)
    default_model_id = adapter._resolve_model_id_from_value(agent.model)
    set_selected_model_id(agent, "selected")
    agent.model = "switched-model"
    assert adapter._restore_agent_default_model(agent) is True
    assert selected_model_id(agent) is None

    session.session_model_id = "switched-model"
    agent.model = "different"
    assert adapter._restore_default_model(agent, session) is True
    assert session.config_values["model"] == default_model_id

    session.session_model_id = None
    cast(Any, agent).model = _INVALID_TEST_VALUE
    assert asyncio.run(adapter._resolve_model_override(session, agent)) is None

    with pytest.raises(RequestError):
        adapter._resolve_runtime_model(agent, model_override=None)
    with pytest.raises(UserError):
        adapter._resolve_runtime_model(agent, model_override="not a model id\n")

    with pytest.raises(RequestError):
        asyncio.run(adapter._resolve_deferred_approvals(session=session, requests=cast(Any, None)))

    assert asyncio.run(adapter._record_cancelled_approval(session, None)) is None
    assert (
        asyncio.run(
            adapter._handle_slash_command("unknown", argument=None, session=session, agent=agent)
        )
        is None
    )

    with pytest.raises(RequestError):
        adapter._resolve_selected_model(" ")
    with pytest.raises(RequestError):
        adapter._resolve_selected_model("codex:   ")

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "codex_auth_helper":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)  # pragma: no cover

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RequestError):
        adapter._resolve_selected_model("codex:gpt-5.4")


def test_sessions_normalize_cwd_and_serialize_mcp_servers(tmp_path: Path) -> None:
    agent = Agent(TestModel(custom_output_text="misc"))
    adapter = _adapter(
        agent=agent,
        config=AdapterConfig(
            available_models=[
                AdapterModel(
                    model_id="default:test",
                    name="Default",
                    override=cast(Any, agent.model),
                ),
            ],
            session_store=MemorySessionStore(),
        ),
    )
    response = asyncio.run(adapter.new_session(cwd="relative/path", mcp_servers=[]))
    session = _stored_session(adapter, response.session_id)

    assert session.cwd == Path.cwd() / "relative/path"
    adapter._update_session_mcp_servers(session, None)
    assert session.mcp_servers == []

    stdio_server = McpServerStdio(name="stdio", command="python", args=["server.py"], env=[])
    http_server = HttpMcpServer(name="http", url="https://example.com", headers=[], type="http")
    sse_server = SseMcpServer(name="sse", url="https://example.com/sse", headers=[], type="sse")

    assert adapter._serialize_mcp_server(stdio_server) == {
        "args": ["server.py"],
        "command": "python",
        "name": "stdio",
        "transport": "stdio",
    }
    assert adapter._serialize_mcp_server(http_server) == {
        "name": "http",
        "transport": "http",
        "url": "https://example.com",
    }
    assert adapter._serialize_mcp_server(sse_server) == {
        "name": "sse",
        "transport": "sse",
        "url": "https://example.com/sse",
    }


def test_prompt_execution_handles_streaming_and_deferred_fallbacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = Agent(TestModel(custom_output_text="unused"), output_type=str)
    adapter = _adapter(
        agent=agent,
        config=AdapterConfig(
            session_store=MemorySessionStore(),
            enable_generic_tool_projection=False,
        ),
    )
    response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    session = _stored_session(adapter, response.session_id)

    assert asyncio.run(adapter._record_tool_updates(session, agent, [])) is None
    assert adapter._normalize_cwd("relative/path") == Path.cwd() / "relative/path"

    async def fake_stream_run(*args: Any, **kwargs: Any) -> tuple[Any, bool]:
        del args, kwargs
        return _fake_run_result("streamed"), True

    monkeypatch.setattr(adapter, "_should_stream_text_responses", lambda *args, **kwargs: True)
    monkeypatch.setattr(adapter, "_run_prompt_with_events", fake_stream_run)
    outcome = asyncio.run(
        adapter._run_prompt(
            agent=agent,
            prompt=[text_block("stream please")],
            session=session,
        )
    )
    assert outcome.result.output == "streamed"
    assert outcome.streamed_output is True

    monkeypatch.setattr(adapter, "_should_stream_text_responses", lambda *args, **kwargs: False)

    async def fake_run(prompt_text: str | None, **kwargs: Any) -> Any:
        del prompt_text, kwargs
        return _fake_run_result("plain")

    cast(Any, agent).run = fake_run
    outcome = asyncio.run(
        adapter._run_prompt(
            agent=agent,
            prompt=[text_block("plain please")],
            session=session,
        )
    )
    assert outcome.result.output == "plain"
    assert outcome.streamed_output is False

    calls: list[int] = []

    async def flaky_run(prompt_text: str | None, **kwargs: Any) -> Any:
        del prompt_text, kwargs
        calls.append(1)
        if len(calls) == 1:
            raise UserError("broken model")
        return _fake_run_result("recovered")

    cast(Any, agent).run = flaky_run
    monkeypatch.setattr(adapter, "_restore_default_model", lambda *args, **kwargs: True)
    outcome = asyncio.run(
        adapter._run_prompt(
            agent=agent,
            prompt=[text_block("recover please")],
            session=session,
        )
    )
    assert outcome.result.output == "recovered"

    no_bridge_result = _fake_run_result(
        DeferredToolRequests(approvals=[ToolCallPart("approval-only", {"x": 1})])
    )

    async def no_bridge_run(prompt_text: str | None, **kwargs: Any) -> Any:
        del prompt_text, kwargs
        return no_bridge_result

    cast(Any, agent).run = no_bridge_run
    monkeypatch.setattr(adapter, "_supports_deferred_approval_bridge", lambda: False)
    no_bridge_outcome = asyncio.run(
        adapter._run_prompt(
            agent=agent,
            prompt=[text_block("approval please")],
            session=session,
        )
    )
    assert no_bridge_outcome.stop_reason == "end_turn"

    bridge_adapter = _adapter(
        agent=agent,
        config=AdapterConfig(
            session_store=MemorySessionStore(),
            approval_bridge=cast(Any, _INVALID_TEST_VALUE),
            enable_generic_tool_projection=False,
        ),
    )
    bridge_response = asyncio.run(bridge_adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    bridge_session = _stored_session(bridge_adapter, bridge_response.session_id)

    async def calls_present_run(prompt_text: str | None, **kwargs: Any) -> Any:
        del prompt_text, kwargs
        return _fake_run_result(
            DeferredToolRequests(
                calls=[ToolCallPart("needs-call", {"x": 1})],
                approvals=[ToolCallPart("needs-approval", {"y": 2})],
            )
        )

    cast(Any, agent).run = calls_present_run
    bridge_outcome = asyncio.run(
        bridge_adapter._run_prompt(
            agent=agent,
            prompt=[text_block("calls please")],
            session=bridge_session,
        )
    )
    assert bridge_outcome.stop_reason == "end_turn"

    async def approval_loop_run(prompt_text: str | None, **kwargs: Any) -> Any:
        del prompt_text, kwargs
        return _fake_run_result(
            DeferredToolRequests(approvals=[ToolCallPart("approval", {"z": 3})])
        )

    cast(Any, agent).run = approval_loop_run

    async def unresolved_approvals(**kwargs: Any) -> Any:
        del kwargs
        return SimpleNamespace(
            cancelled=False,
            cancelled_tool_call=None,
            deferred_tool_results=None,
        )

    monkeypatch.setattr(
        bridge_adapter,
        "_resolve_deferred_approvals",
        unresolved_approvals,
    )
    with pytest.raises(RequestError):
        asyncio.run(
            bridge_adapter._run_prompt(
                agent=agent,
                prompt=[text_block("loop please")],
                session=bridge_session,
            )
        )

    async def empty_stream(prompt_text: str | None, **kwargs: Any):
        del prompt_text, kwargs
        yield FunctionToolResultEvent(RetryPromptPart("retry", tool_name=None))
        yield FunctionToolResultEvent(ToolReturnPart("final_result", "done"))

    cast(Any, agent).run_stream_events = empty_stream
    with pytest.raises(RequestError):
        asyncio.run(
            type(adapter)._run_prompt_with_events(
                adapter,
                agent=agent,
                prompt_input="stream",
                run_kwargs={},
                session=session,
            )
        )


def test_prompt_execution_compaction_paths_cover_empty_updates_and_retry_skips(
    tmp_path: Path,
) -> None:
    agent = Agent(TestModel(custom_output_text="unused"), output_type=str)
    adapter = _adapter(
        agent=agent,
        config=AdapterConfig(
            session_store=MemorySessionStore(),
            capability_bridges=[OpenAICompactionBridge(message_count_threshold=1)],
        ),
    )
    response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    session = _stored_session(adapter, response.session_id)
    execution = adapter._prompt_runtime._execution

    assert execution._skip_compaction_providers() == frozenset({"openai"})

    recorded_updates: list[Any] = []

    async def record_update(_session: Any, update: Any) -> None:
        recorded_updates.append(update)  # pragma: no cover

    async def record_bridge_updates(_session: Any, _agent: Any) -> None:
        return None

    adapter._prompt_runtime._record_update = record_update
    execution.record_bridge_updates = record_bridge_updates

    assert asyncio.run(execution.record_tool_updates(session, agent, [])) is None
    assert recorded_updates == []

    adapter._should_stream_text_responses = lambda *args, **kwargs: True
    assert (
        asyncio.run(
            execution._record_execution_updates(
                session=session,
                agent=agent,
                result=_fake_run_result("streamed"),
                model_override=None,
                run_output_type=None,
            )
        )
        is None
    )
    assert recorded_updates == []

    async def retry_only_stream(prompt_text: str | None, **kwargs: Any):
        del prompt_text, kwargs
        yield FunctionToolResultEvent(RetryPromptPart("retry", tool_name=None))

    cast(Any, agent).run_stream_events = retry_only_stream
    with pytest.raises(RequestError):
        asyncio.run(
            type(adapter)._run_prompt_with_events(
                adapter,
                agent=agent,
                prompt_input="stream",
                run_kwargs={},
                session=session,
            )
        )


def test_prompt_execution_records_visible_compaction_updates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    agent = Agent(TestModel(custom_output_text="unused"), output_type=str)
    adapter = _adapter(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    session = _stored_session(adapter, response.session_id)
    execution = adapter._prompt_runtime._execution
    recorded_updates: list[Any] = []

    async def record_update(_session: Any, update: Any) -> None:
        recorded_updates.append(update)

    async def record_bridge_updates(_session: Any, _agent: Any) -> None:
        return None

    monkeypatch.setattr(
        prompt_execution_module,
        "build_compaction_updates",
        lambda *args, **kwargs: [
            SimpleNamespace(kind="compaction", args=args, kwargs=kwargs),
        ],
    )
    adapter._prompt_runtime._record_update = record_update
    execution.record_bridge_updates = record_bridge_updates

    asyncio.run(execution.record_tool_updates(session, agent, []))
    assert len(recorded_updates) == 1

    recorded_updates.clear()
    adapter._should_stream_text_responses = lambda *args, **kwargs: True
    asyncio.run(
        execution._record_execution_updates(
            session=session,
            agent=agent,
            result=_fake_run_result("streamed"),
            model_override=None,
            run_output_type=None,
        )
    )
    assert len(recorded_updates) == 1


def test_cancel_stops_active_prompt_and_persists_terminal_history(
    tmp_path: Path,
) -> None:
    async def run_scenario() -> None:
        agent = Agent(TestModel(custom_output_text="unused"), output_type=str)
        adapter = _adapter(
            agent=agent,
            config=AdapterConfig(session_store=MemorySessionStore()),
        )
        client = RecordingClient()
        adapter.on_connect(client)

        response = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def blocking_run_prompt(*, agent: Any, prompt: Any, session: Any) -> Any:
            del agent, prompt, session
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            raise AssertionError("prompt task should have been cancelled")  # pragma: no cover

        object.__setattr__(adapter, "_run_prompt", blocking_run_prompt)

        prompt_task = asyncio.create_task(
            adapter.prompt(
                prompt=[text_block("Long running task")],
                session_id=response.session_id,
            )
        )
        await started.wait()

        await adapter.cancel(response.session_id)
        prompt_response = await prompt_task

        assert cancelled.is_set() is True
        assert prompt_response.stop_reason == "cancelled"

        stored_session = _stored_session(adapter, response.session_id)
        assert stored_session.message_history_json is not None
        history = load_message_history(stored_session.message_history_json)
        assert isinstance(history[-1], ModelResponse)
        assert any(
            isinstance(part, TextPart) and "User stopped the run." in part.content
            for part in history[-1].parts
        )
        assert agent_message_texts(client)[-1].startswith("User stopped the run.")

    asyncio.run(run_scenario())


def test_prompt_runtime_handles_edge_cases_without_corrupting_session_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = Agent(TestModel(custom_output_text="unused"), output_type=str)
    adapter = _adapter(
        agent=agent,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    session = _stored_session(adapter, response.session_id)

    async def failing_execute(**kwargs: Any) -> Any:
        del kwargs
        raise UserError("broken model")

    monkeypatch.setattr(adapter._prompt_runtime, "_execute_prompt", failing_execute)
    monkeypatch.setattr(adapter, "_restore_default_model", lambda *args, **kwargs: False)
    with pytest.raises(UserError, match="broken model"):
        asyncio.run(
            adapter._run_prompt(
                agent=agent,
                prompt=[text_block("recover")],
                session=session,
            )
        )

    assert adapter._build_run_kwargs(
        message_history=None,
        deferred_tool_results=None,
        deps=None,
        model_override=None,
        model_settings=None,
        output_type=None,
    ) == {
        "deferred_tool_results": None,
        "message_history": None,
        "model": None,
    }
    adapter._config.approval_bridge = None
    assert adapter._build_run_output_type(agent, session=session) is None
    assert adapter._contains_text_output(NativePlanGeneration) is True
    assert adapter._contains_text_output(cast(Any, _INVALID_TEST_VALUE)) is False
    assert adapter._contains_native_plan_generation([int, NativePlanGeneration]) is True
    assert adapter._contains_native_plan_generation([int, float]) is False

    tool_start_event = PartStartEvent(index=0, part=ToolCallPart("echo", {"text": "hi"}))
    tool_delta_event = PartDeltaEvent(index=0, delta=cast(Any, _INVALID_TEST_VALUE))
    assert adapter._text_chunk_from_event(tool_start_event) is None
    assert adapter._text_chunk_from_event(tool_delta_event) is None
    assert (
        adapter._text_chunk_from_event(PartStartEvent(index=1, part=TextPart("hello"))) == "hello"
    )

    session.plan_entries = []
    session.plan_markdown = "# Saved Plan"
    assert adapter._format_native_plan(session) == "# Saved Plan"
    assert asyncio.run(adapter._prompt_runtime._emit_native_plan_update(session)) is None
    assert adapter._consume_native_plan_update(session) is False

    with pytest.raises(RequestError):
        adapter._prompt_runtime._replace_native_plan_entry(session, index=1, status="completed")

    session.plan_entries = [
        PlanEntry(content="Pending task", priority="low", status="pending").model_dump(mode="json")
    ]
    with pytest.raises(RequestError):
        adapter._prompt_runtime._replace_native_plan_entry(session, index=2, status="completed")

    updated_entry = adapter._prompt_runtime._replace_native_plan_entry(
        session,
        index=1,
        content="Updated task",
        priority="high",
    )
    assert updated_entry.content == "Updated task"
    assert updated_entry.priority == "high"
    assert updated_entry.status == "pending"

    tool_agent = Agent(TestModel(custom_output_text="unused"), output_type=str)
    tool_adapter = _adapter(
        agent=tool_agent,
        config=AdapterConfig(
            approval_bridge=None,
            session_store=MemorySessionStore(),
        ),
    )
    tool_adapter._install_native_plan_tools(tool_agent)
    tools = tool_agent._function_toolset.tools
    get_plan_tool = cast(Any, tools["acp_get_plan"])
    set_plan_tool = cast(Any, tools["acp_set_plan"])
    update_plan_tool = cast(Any, tools["acp_update_plan_entry"])
    mark_done_tool = cast(Any, tools["acp_mark_plan_done"])

    session.config_values["mode"] = "ask"
    assert get_plan_tool.prepare(None, get_plan_tool.function_schema) is None
    assert get_plan_tool.function() == "No active ACP session is bound."
    assert set_plan_tool.prepare(None, set_plan_tool.function_schema) is None
    assert asyncio.run(update_plan_tool.function(index=1)) == "No active ACP session is bound."
    assert asyncio.run(mark_done_tool.function(index=1)) == "No active ACP session is bound."

    async def retry_and_output_stream(prompt_text: str | None, **kwargs: Any):
        del prompt_text, kwargs
        yield FunctionToolResultEvent(RetryPromptPart("retry", tool_name="output"))
        yield FunctionToolResultEvent(ToolReturnPart("final_result", "done"))

    cast(Any, agent).run_stream_events = retry_and_output_stream
    with pytest.raises(RequestError):
        asyncio.run(
            type(adapter)._run_prompt_with_events(
                adapter,
                agent=agent,
                prompt_input="stream",
                run_kwargs={},
                session=session,
            )
        )


def test_native_plan_additional_instructions_append_without_replacing_core_guidance(
    tmp_path: Path,
) -> None:
    agent = Agent(TestModel(custom_output_text="wrapped"), output_type=str)
    adapter = _adapter(
        agent=agent,
        config=AdapterConfig(
            native_plan_additional_instructions=(
                "Keep plans concise.\nOnly use in-progress for multi-turn work."
            ),
            session_store=MemorySessionStore(),
        ),
    )
    response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    session = _stored_session(adapter, response.session_id)

    session.plan_entries = [
        PlanEntry(content="Inspect repo", priority="medium", status="pending").model_dump(
            mode="json"
        )
    ]

    rendered = adapter._format_native_plan(session)

    assert "Use these 1-based entry numbers with `acp_update_plan_entry`" in rendered
    assert "Additional plan instructions:" in rendered
    assert "Keep plans concise." in rendered
    assert "Only use in-progress for multi-turn work." in rendered
    assert "1. [pending] (medium) Inspect repo" in rendered


def test_native_plan_runtime_covers_empty_state_and_markdown_only_paths(
    tmp_path: Path,
) -> None:
    agent = Agent(TestModel(custom_output_text="wrapped"), output_type=str)
    adapter = _adapter(
        agent=agent,
        config=AdapterConfig(
            capability_bridges=[
                PrepareToolsBridge(
                    default_mode_id="plan",
                    modes=[
                        PrepareToolsMode(
                            id="plan",
                            name="Plan",
                            description="Structured plan mode.",
                            prepare_func=lambda ctx, tool_defs: list(tool_defs),
                            plan_mode=True,
                        )
                    ],
                )
            ],
            native_plan_additional_instructions="Keep the plan reviewable.",
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)
    response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    session = _stored_session(adapter, response.session_id)
    native_plan_runtime = adapter._prompt_runtime._native_plan_runtime

    assert asyncio.run(native_plan_runtime.emit_native_plan_update(session)) is None
    assert (
        asyncio.run(native_plan_runtime.persist_current_native_plan_state(session, agent=agent))
        is None
    )

    session.plan_markdown = "# Saved plan\n"
    assert native_plan_runtime.format_native_plan(session) == (
        "# Saved plan\n\nAdditional plan instructions:\n\nKeep the plan reviewable."
    )

    adapter._config.native_plan_additional_instructions = "   "
    assert native_plan_runtime.format_native_plan(session) == "# Saved plan\n"


def test_native_plan_tool_prepare_paths_cover_transient_session_loss(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tool_agent = Agent(TestModel(custom_output_text="unused"), output_type=str)
    tool_adapter = _adapter(
        agent=tool_agent,
        config=AdapterConfig(
            capability_bridges=[
                PrepareToolsBridge(
                    default_mode_id="agent",
                    modes=[
                        PrepareToolsMode(
                            id="agent",
                            name="Agent",
                            description="Execution mode with plan progress tools.",
                            prepare_func=lambda ctx, tool_defs: list(tool_defs),
                            plan_tools=True,
                        )
                    ],
                )
            ],
            session_store=MemorySessionStore(),
        ),
    )
    response = asyncio.run(tool_adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    session = _stored_session(tool_adapter, response.session_id)
    tool_adapter._install_native_plan_tools(tool_agent)
    tools = tool_agent._function_toolset.tools
    set_plan_tool = cast(Any, tools["acp_set_plan"])
    update_plan_tool = cast(Any, tools["acp_update_plan_entry"])

    calls = {"count": 0}

    def flaky_try_active_session(_agent: Any) -> Any:
        calls["count"] += 1
        return session if calls["count"] == 1 else None

    monkeypatch.setattr(native_plan_runtime_module, "try_active_session", flaky_try_active_session)

    assert update_plan_tool.prepare(None, update_plan_tool.function_schema) is None
    calls["count"] = 0
    assert set_plan_tool.prepare(None, set_plan_tool.function_schema) is None

    monkeypatch.setattr(native_plan_runtime_module, "try_active_session", lambda _agent: None)
    assert asyncio.run(set_plan_tool.function(entries=[])) == "No active ACP session is bound."


def test_prompt_model_override_provider_can_switch_model_for_media_prompts(
    tmp_path: Path,
) -> None:
    class DemoPromptModelOverrideProvider:
        def get_prompt_model_override(self, session, agent, prompt, model_override):
            del session, agent
            if any(isinstance(block, ImageContentBlock) for block in prompt):
                return "google-gla:gemini-3-flash-preview"
            return model_override  # pragma: no cover

    agent = Agent(TestModel(custom_output_text="ok"))
    adapter = _adapter(
        agent=agent,
        config=AdapterConfig(
            prompt_model_override_provider=DemoPromptModelOverrideProvider(),
            session_store=MemorySessionStore(),
        ),
    )
    response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    session = _stored_session(adapter, response.session_id)

    resolved_override = asyncio.run(
        adapter._resolve_prompt_model_override(
            session,
            agent,
            prompt=[
                text_block("explain this image"),
                ImageContentBlock(type="image", data="aGVsbG8=", mime_type="image/png"),
            ],
            model_override="openrouter:google/gemini-3-flash-preview",
        )
    )

    assert resolved_override == "google-gla:gemini-3-flash-preview"


def test_workspace_prompt_model_provider_prefers_explicit_media_override_for_image_prompts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACP_TRAVEL_MEDIA_MODEL", "openai:gpt-4.1-mini")
    monkeypatch.delenv("TRAVEL_MEDIA_MODEL", raising=False)
    monkeypatch.setenv("MODEL_NAME", "openrouter:google/gemini-3-flash-preview")

    provider = TravelPromptModelProvider()
    session = cast(Any, object())
    agent = cast(Any, object())

    resolved_override = provider.get_prompt_model_override(
        session,
        agent,
        prompt=[
            text_block("describe"),
            ImageContentBlock(type="image", data="aGVsbG8=", mime_type="image/png"),
        ],
        model_override="openrouter:google/gemini-3-flash-preview",
    )

    assert resolved_override == "openai:gpt-4.1-mini"


def test_adapter_wrapper_methods_delegate_to_runtime_components(tmp_path: Path) -> None:
    agent = Agent(TestModel(custom_output_text="wrapped"), output_type=str)
    adapter = _adapter(agent=agent, config=AdapterConfig(session_store=MemorySessionStore()))
    response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    session = _stored_session(adapter, response.session_id)

    active_calls: list[tuple[Any, Any]] = []
    apply_calls: list[tuple[Any, Any]] = []
    set_model_calls: list[tuple[Any, Any, Any]] = []

    def fake_set_active_session(a: Any, s: Any) -> None:
        active_calls.append((a, s))

    def fake_bind_session_client(s: Any) -> Any:
        return s

    async def fake_build_config_options(*args: Any, **kwargs: Any) -> list[str]:
        del args, kwargs
        return ["cfg"]

    async def fake_get_mode_state(*args: Any, **kwargs: Any) -> str:
        del args, kwargs
        return "mode"

    def fake_synchronize_mode_state(*args: Any, **kwargs: Any) -> None:
        del args, kwargs  # pragma: no cover

    async def fake_get_provider_config_options(*args: Any, **kwargs: Any) -> list[str]:
        del args, kwargs
        return ["opt"]

    async def fake_get_plan_entries(*args: Any, **kwargs: Any) -> list[str]:
        del args, kwargs
        return ["plan"]

    async def fake_synchronize_session_metadata(*args: Any, **kwargs: Any) -> None:
        del args, kwargs

    async def fake_get_approval_state(*args: Any, **kwargs: Any) -> dict[str, bool]:
        del args, kwargs
        return {"ok": True}

    def fake_find_model_option(*args: Any, **kwargs: Any) -> str:
        del args, kwargs
        return "model-option"

    def fake_require_model_option(*args: Any, **kwargs: Any) -> str:
        del args, kwargs
        return "required-model"

    def fake_supports_fallback_model_selection() -> bool:
        return True

    def fake_model_identity(value: Any) -> str:
        return f"id:{value}"

    def fake_apply_session_model_to_agent(a: Any, s: Any) -> None:
        apply_calls.append((a, s))

    def fake_set_agent_model(a: Any, s: Any, model_id: Any) -> None:
        set_model_calls.append((a, s, model_id))

    def fake_known_tool_call_starts(s: Any) -> dict[str, str]:
        del s
        return {"call": "start"}

    def fake_supports_streaming_model(*args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        return True

    def fake_native_plan_bridge(s: Any) -> str:
        del s
        return "bridge"

    def fake_supports_native_plan_state(s: Any) -> bool:
        del s
        return True

    adapter._session_runtime._set_active_session = fake_set_active_session
    adapter._session_runtime._bind_session_client = fake_bind_session_client
    adapter._session_runtime._build_config_options = fake_build_config_options
    adapter._session_runtime._get_mode_state = fake_get_mode_state
    adapter._session_runtime._synchronize_mode_state = fake_synchronize_mode_state
    adapter._session_runtime._get_provider_config_options = fake_get_provider_config_options
    adapter._session_runtime._get_plan_entries = fake_get_plan_entries
    adapter._session_runtime._synchronize_session_metadata = fake_synchronize_session_metadata
    adapter._session_runtime._get_approval_state = fake_get_approval_state
    adapter._session_runtime._find_model_option = fake_find_model_option
    adapter._session_runtime._require_model_option = fake_require_model_option
    adapter._session_runtime._supports_fallback_model_selection = (
        fake_supports_fallback_model_selection
    )
    adapter._session_runtime._model_identity = fake_model_identity
    adapter._session_runtime._apply_session_model_to_agent = fake_apply_session_model_to_agent
    adapter._session_runtime._set_agent_model = fake_set_agent_model

    adapter._prompt_runtime._known_tool_call_starts = fake_known_tool_call_starts
    adapter._prompt_runtime._supports_streaming_model = fake_supports_streaming_model
    adapter._prompt_runtime._native_plan_bridge = fake_native_plan_bridge
    adapter._prompt_runtime._supports_native_plan_state = fake_supports_native_plan_state

    assert adapter._known_tool_call_starts(session) == {"call": "start"}
    adapter._set_active_session(agent, session)
    assert active_calls == [(agent, session)]
    assert adapter._bind_session_client(session) is session
    assert asyncio.run(
        adapter._build_config_options(
            session,
            agent,
            model_selection_state=None,
            mode_state=None,
        )
    ) == ["cfg"]
    assert asyncio.run(adapter._get_mode_state(session, agent)) == "mode"
    assert asyncio.run(adapter._get_provider_config_options(session, agent)) == ["opt"]
    assert asyncio.run(adapter._get_plan_entries(session, agent)) == ["plan"]
    assert asyncio.run(adapter._synchronize_session_metadata(session, agent)) is None
    assert asyncio.run(adapter._get_approval_state(session, agent)) == {"ok": True}
    assert adapter._find_model_option("demo") == "model-option"
    assert adapter._require_model_option("demo") == "required-model"
    assert adapter._supports_fallback_model_selection() is True
    assert adapter._supports_streaming_model(agent, model_override=None) is True
    assert adapter._native_plan_bridge(session) == "bridge"
    assert adapter._supports_native_plan_state(session) is True
    assert adapter._model_identity("demo") == "id:demo"
    adapter._apply_session_model_to_agent(agent, session)
    adapter._set_agent_model(agent, session, "model-a")
    assert apply_calls == [(agent, session)]
    assert set_model_calls == [(agent, session, "model-a")]


def test_prompt_runtime_and_session_surface_cover_remaining_helper_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = Agent(TestModel(custom_output_text="helpers"), output_type=str)
    adapter = _adapter(agent=agent, config=AdapterConfig(session_store=MemorySessionStore()))
    response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    session = _stored_session(adapter, response.session_id)

    native_plan_calls: list[tuple[str, Any]] = []

    def fake_supports_native_plan_progress(s: Any) -> bool:
        native_plan_calls.append(("supports_progress", s))
        return True

    async def fake_persist_external_plan_state(
        session: Any, *, agent: Any, entries: Any, plan_markdown: Any
    ) -> None:
        native_plan_calls.append(
            ("persist_external", (session, agent, list(entries), plan_markdown))
        )

    async def fake_persist_native_plan_state(
        session: Any, *, agent: Any, entries: Any, plan_markdown: Any
    ) -> None:
        native_plan_calls.append(("persist_native", (session, agent, list(entries), plan_markdown)))

    adapter._prompt_runtime._native_plan_runtime.supports_native_plan_progress = (
        fake_supports_native_plan_progress
    )
    adapter._prompt_runtime._native_plan_runtime.persist_external_plan_state = (
        fake_persist_external_plan_state
    )
    adapter._prompt_runtime._native_plan_runtime.persist_native_plan_state = (
        fake_persist_native_plan_state
    )

    entries = [PlanEntry(content="One", priority="medium", status="pending")]
    assert adapter._prompt_runtime._known_tool_call_starts(session) == {}
    assert adapter._prompt_runtime._supports_native_plan_state(session) is False
    assert adapter._prompt_runtime._requires_native_plan_output(session) is False
    assert adapter._prompt_runtime._supports_native_plan_progress(session) is True
    assert adapter._prompt_runtime._get_native_plan_entries(session) is None
    asyncio.run(
        adapter._prompt_runtime._persist_external_plan_state(
            session,
            agent=agent,
            entries=entries,
            plan_markdown="# External",
        )
    )
    asyncio.run(
        adapter._prompt_runtime._persist_native_plan_state(
            session,
            agent=agent,
            entries=entries,
            plan_markdown="# Native",
        )
    )
    assert [call[0] for call in native_plan_calls] == [
        "supports_progress",
        "persist_external",
        "persist_native",
    ]

    prompt_model_runtime = adapter._prompt_runtime._model_runtime
    deferred_agent = cast(Any, SimpleNamespace(output_type=DeferredToolRequests))
    assert (
        prompt_model_runtime.build_run_output_type(deferred_agent, session=session)
        is DeferredToolRequests
    )

    hook_context = prompt_model_runtime.hook_context(agent=agent, session=session)
    with hook_context:
        pass

    no_hook_agent = Agent(TestModel(custom_output_text="helpers"), output_type=str)
    no_hook_adapter = _adapter(
        agent=no_hook_agent,
        config=AdapterConfig(
            session_store=MemorySessionStore(),
            hook_projection_map=None,
        ),
    )
    no_hook_response = asyncio.run(no_hook_adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    no_hook_session = _stored_session(no_hook_adapter, no_hook_response.session_id)
    with no_hook_adapter._prompt_runtime._model_runtime.hook_context(
        agent=no_hook_agent,
        session=no_hook_session,
    ):
        pass

    def fail_infer_model(value: Any) -> Any:
        raise ValueError(f"invalid model: {value}")

    monkeypatch.setattr(pydantic_models, "infer_model", fail_infer_model)
    with pytest.raises(UserError, match="invalid model"):
        prompt_model_runtime.resolve_runtime_model(agent, model_override="broken:model")

    assert prompt_model_runtime.contains_native_plan_generation([str, TaskPlan]) is True
    assert prompt_model_runtime.contains_native_plan_generation("text") is False

    session_model_runtime = adapter._session_runtime._model_runtime
    session_surface_runtime = adapter._session_runtime._surface_runtime

    async def unavailable_mode(mode_id: str, session_id: str) -> None:
        del mode_id, session_id
        return None

    async def unavailable_config(config_id: str, session_id: str, value: Any) -> None:
        del config_id, session_id, value
        return None

    adapter._session_runtime.set_session_mode = unavailable_mode
    adapter._session_runtime.set_config_option = unavailable_config

    async def fake_mode_state(*args: Any, **kwargs: Any) -> ModeState:
        del args, kwargs
        return ModeState(modes=[SessionMode(id="plan", name="Plan")], current_mode_id="plan")

    adapter._session_runtime._get_mode_state = fake_mode_state
    assert (
        asyncio.run(
            session_model_runtime.handle_slash_command(
                "plan",
                argument=None,
                session=session,
                agent=agent,
            )
        )
        == "Mode `plan` is unavailable"
    )
    assert (
        asyncio.run(
            session_model_runtime.handle_slash_command(
                "thinking",
                argument="high",
                session=session,
                agent=agent,
            )
        )
        == "Thinking effort is unavailable or invalid"
    )

    adapter._remember_default_model(agent)
    assign_model(agent, "restored-model")
    session.session_model_id = "restored-model"
    session.config_values["model"] = "restored-model"
    assert adapter._restore_default_model(agent, session) is True
    assert session.session_model_id == "test"
    assert session.config_values["model"] == "test"

    monkeypatch.setattr(
        adapter._session_runtime, "_resolve_model_id_from_value", lambda value: None
    )
    assign_model(agent, "restored-model")
    session.session_model_id = "restored-model"
    session.config_values["model"] = "restored-model"
    assert adapter._restore_default_model(agent, session) is True
    assert session.session_model_id is None
    assert "model" not in session.config_values

    assert (
        asyncio.run(session_surface_runtime.set_provider_model_state(session, agent, "x")) is None
    )
    assert (
        asyncio.run(
            session_surface_runtime.set_provider_config_options(
                session,
                agent,
                "flag",
                True,
            )
        )
        is False
    )
    assert session_surface_runtime.current_thinking_value(session, agent) is None

    monkeypatch.setattr(
        type(adapter._bridge_manager),
        "get_config_options",
        lambda self, s, a: [
            SessionConfigOptionBoolean(
                id="thinking",
                name="Thinking",
                current_value=True,
                type="boolean",
            ),
        ],
    )
    assert session_surface_runtime.current_thinking_value(session, agent) is None
    assert session_surface_runtime.plan_storage_metadata(session) is None

    with pytest.raises(ValueError):
        session_surface_runtime.require_model_option("missing-model")

    adapter._config.available_models = [
        AdapterModel(model_id="raw-model", name="Raw Model", override=cast(Any, agent.model))
    ]
    assert session_surface_runtime.require_model_option("raw-model").model_id == "raw-model"

    assert session_surface_runtime.resolve_model_id_from_value("raw-model") == "raw-model"
    monkeypatch.setattr(adapter._session_runtime, "_model_identity", lambda value: None)
    assert session_surface_runtime.resolve_model_id_from_value("raw-model") == "raw-model"

    client = RecordingClient()
    adapter.on_connect(client)
    asyncio.run(
        session_surface_runtime.emit_session_state_updates(
            session,
            SessionSurface(
                config_options=None,
                model_state=None,
                mode_state=None,
                plan_entries=None,
            ),
            emit_available_commands=False,
            emit_config_options=False,
            emit_current_mode=True,
            emit_plan=False,
            emit_session_info=False,
        )
    )
    assert client.updates == []

    async def no_model_selection_state(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        return None

    monkeypatch.setattr(
        session_surface_runtime,
        "get_model_selection_state",
        no_model_selection_state,
    )
    session.session_model_id = "unconfigured-model"
    clear_selected_model_id(agent)
    assert (
        asyncio.run(session_surface_runtime.resolve_model_override(session, agent))
        == "unconfigured-model"
    )
    assert adapter._prompt_runtime._supports_streaming_model(agent, model_override=None) is True
    adapter._set_native_plan_state(
        session,
        entries=[PlanEntry(content="Updated", priority="high", status="pending")],
        plan_markdown="# Updated",
    )
    assert session.plan_markdown == "# Updated"


def test_adapter_prompt_handler_covers_prompt_response_and_no_current_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="ok")),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    session_response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    handler = cast(Any, adapter)._adapter_prompt

    adapter_prompt_module = importlib.import_module("pydantic_acp.runtime._adapter_prompt")
    monkeypatch.setattr(adapter_prompt_module.asyncio, "current_task", lambda: None)

    async def fake_run_prompt(**kwargs: Any) -> PromptResponse:
        del kwargs
        return PromptResponse(stop_reason="end_turn", usage=None, user_message_id="ignored")

    handler._run_prompt = fake_run_prompt
    response = asyncio.run(
        handler.prompt(
            [text_block("hello")],
            session_response.session_id,
            message_id="prompt-msg",
        )
    )
    assert response.user_message_id == "prompt-msg"
    assert cast(Any, adapter)._active_prompt_tasks == {}

    cancelled = asyncio.run(
        handler._handle_cancelled_prompt(
            session=_stored_session(adapter, session_response.session_id),
            prompt_text="cancel me",
        )
    )
    assert cancelled.stop_reason == "cancelled"


def test_unknown_slash_command_falls_through_to_prompt_execution(
    tmp_path: Path,
) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="continued after slash")),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("/unknown keep going")],
            session_id=session.session_id,
        )
    )

    assert agent_message_texts(client) == ["continued after slash"]


def test_adapter_public_setters_cover_none_provider_and_mode_paths(
    tmp_path: Path,
) -> None:
    class EmptyModelsProvider:
        def set_model(self, session, agent, model_id):
            del session, agent, model_id
            return None

        def get_model_state(self, session, agent):
            del session, agent
            return None

    class ReviewModesProvider:
        def get_mode_state(self, session, agent):
            del session, agent
            return ModeState(
                current_mode_id="review",
                modes=[SessionMode(id="review", name="Review")],
            )

        def set_mode(self, session, agent, mode_id):
            del session, agent, mode_id
            return ModeState(
                current_mode_id="review",
                modes=[SessionMode(id="review", name="Review")],
            )

    model_agent = Agent(TestModel(custom_output_text="model"))
    model_adapter = _adapter(
        agent=model_agent,
        config=AdapterConfig(
            models_provider=EmptyModelsProvider(),
            session_store=MemorySessionStore(),
        ),
    )
    model_response = asyncio.run(model_adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    assert (
        asyncio.run(model_adapter.set_session_model("openai:gpt-5.4", model_response.session_id))
        is None
    )

    mode_agent = Agent(TestModel(custom_output_text="mode"))
    mode_adapter = _adapter(
        agent=mode_agent,
        config=AdapterConfig(
            modes_provider=ReviewModesProvider(),
            session_store=MemorySessionStore(),
        ),
    )
    mode_response = asyncio.run(mode_adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    assert (
        asyncio.run(mode_adapter.set_config_option("mode", mode_response.session_id, "review"))
        is not None
    )
