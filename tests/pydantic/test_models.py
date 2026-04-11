from __future__ import annotations as _annotations

import asyncio
import json

import pytest
from acp.exceptions import RequestError
from pydantic_ai import ModelRequest, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.messages import ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from .support import (
    AdapterConfig,
    AdapterModel,
    Agent,
    AgentPlanUpdate,
    AsyncDemoConfigOptionsProvider,
    AsyncDemoModesProvider,
    AsyncDemoPlanProvider,
    ConfigOptionUpdate,
    CurrentModeUpdate,
    DemoConfigOptionsProvider,
    DemoModelsProvider,
    DemoModesProvider,
    DemoPlanProvider,
    FreeformModelsProvider,
    MemorySessionStore,
    Path,
    PrepareToolsBridge,
    PrepareToolsMode,
    RecordingClient,
    ReservedModelConfigProvider,
    RunContext,
    SessionConfigOptionBoolean,
    SessionInfoUpdate,
    TestModel,
    ToolDefinition,
    agent_message_texts,
    create_acp_agent,
    text_block,
)


def test_new_session_exposes_model_state_and_model_config_option(
    tmp_path: Path,
) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="default", model_name="model-a")),
        config=AdapterConfig(
            allow_model_selection=True,
            available_models=[
                AdapterModel(
                    model_id="model-a",
                    name="Model A",
                    override=TestModel(custom_output_text="default", model_name="model-a"),
                ),
                AdapterModel(
                    model_id="model-b",
                    name="Model B",
                    override=TestModel(custom_output_text="switched", model_name="model-b"),
                ),
            ],
            session_store=MemorySessionStore(),
        ),
    )

    new_session_response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    assert new_session_response.models is not None
    assert new_session_response.models.current_model_id == "model-a"
    assert [model.model_id for model in new_session_response.models.available_models] == [
        "model-a",
        "model-b",
    ]
    assert new_session_response.config_options is not None
    assert new_session_response.config_options[0].id == "model"
    assert new_session_response.config_options[0].current_value == "model-a"


def test_session_model_override_is_session_local(tmp_path: Path) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="default", model_name="model-a")),
        config=AdapterConfig(
            allow_model_selection=True,
            available_models=[
                AdapterModel(
                    model_id="model-a",
                    name="Model A",
                    override=TestModel(custom_output_text="default", model_name="model-a"),
                ),
                AdapterModel(
                    model_id="model-b",
                    name="Model B",
                    override=TestModel(custom_output_text="switched", model_name="model-b"),
                ),
            ],
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    first_session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    second_session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(adapter.set_session_model(model_id="model-b", session_id=second_session.session_id))

    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Use the default model.")],
            session_id=first_session.session_id,
        )
    )
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Use the overridden model.")],
            session_id=second_session.session_id,
        )
    )

    assert agent_message_texts(client) == ["default", "switched"]


def test_set_config_option_model_updates_current_model(tmp_path: Path) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="default", model_name="model-a")),
        config=AdapterConfig(
            allow_model_selection=True,
            available_models=[
                AdapterModel(
                    model_id="model-a",
                    name="Model A",
                    override=TestModel(custom_output_text="default", model_name="model-a"),
                ),
                AdapterModel(
                    model_id="model-b",
                    name="Model B",
                    override=TestModel(custom_output_text="switched", model_name="model-b"),
                ),
            ],
            session_store=MemorySessionStore(),
        ),
    )
    new_session_response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    config_response = asyncio.run(
        adapter.set_config_option(
            config_id="model",
            session_id=new_session_response.session_id,
            value="model-b",
        )
    )

    assert config_response is not None
    assert config_response.config_options[0].current_value == "model-b"


def test_provider_backed_surface_exposes_modes_config_and_plan(tmp_path: Path) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="provider:default")),
        config=AdapterConfig(
            config_options_provider=DemoConfigOptionsProvider(),
            models_provider=DemoModelsProvider(),
            modes_provider=DemoModesProvider(),
            plan_provider=DemoPlanProvider(),
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    new_session_response = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    assert new_session_response.models is not None
    assert new_session_response.models.current_model_id == "provider-model-a"
    assert new_session_response.modes is not None
    assert new_session_response.modes.current_mode_id == "chat"
    assert [mode.id for mode in new_session_response.modes.available_modes] == [
        "chat",
        "review",
    ]
    assert new_session_response.config_options is not None
    assert [option.id for option in new_session_response.config_options] == [
        "model",
        "mode",
        "stream_enabled",
    ]
    assert new_session_response.config_options[0].name == "Provider Model"

    plan_updates = [update for _, update in client.updates if isinstance(update, AgentPlanUpdate)]
    assert len(plan_updates) == 1
    assert [entry.content for entry in plan_updates[0].entries] == [
        "mode:chat",
        "stream:false",
    ]
    session_info_updates = [
        update for _, update in client.updates if isinstance(update, SessionInfoUpdate)
    ]
    assert session_info_updates
    assert session_info_updates[-1].field_meta is not None
    assert session_info_updates[-1].field_meta["pydantic_acp"]["plan_storage"] == {
        "directory": str(tmp_path / ".acpkit" / "plans")
    }

    client.updates.clear()
    resume_response = asyncio.run(
        adapter.resume_session(
            cwd=str(tmp_path),
            session_id=new_session_response.session_id,
            mcp_servers=[],
        )
    )

    assert resume_response.modes is not None
    assert resume_response.modes.current_mode_id == "chat"
    resumed_plan_updates = [
        update for _, update in client.updates if isinstance(update, AgentPlanUpdate)
    ]
    assert len(resumed_plan_updates) == 1
    assert [entry.content for entry in resumed_plan_updates[0].entries] == [
        "mode:chat",
        "stream:false",
    ]


def test_plan_mode_can_record_native_plan_entries_via_internal_tool(
    tmp_path: Path,
) -> None:
    def expose_tools(
        ctx: RunContext[None],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        del ctx
        return list(tool_defs)

    def route_plan_tool(
        messages: list[ModelRequest | ModelResponse],
        info: AgentInfo,
    ) -> ModelResponse:
        del info
        if messages and isinstance(messages[-1], ModelRequest):
            tool_returns = [part for part in messages[-1].parts if isinstance(part, ToolReturnPart)]
            if tool_returns:
                return ModelResponse(
                    parts=[
                        TextPart(
                            json.dumps(
                                {
                                    "plan_md": "# Plan\n\n1. Inspect the repo\n2. Write the plan\n",
                                    "plan_entries": [
                                        {
                                            "content": "Inspect the repository structure",
                                            "priority": "high",
                                            "status": "in_progress",
                                        },
                                        {
                                            "content": "Review the implementation constraints",
                                            "priority": "medium",
                                            "status": "pending",
                                        },
                                    ],
                                }
                            )
                        )
                    ]
                )
        for message in reversed(messages):
            if not isinstance(message, ModelRequest):
                continue
            for part in reversed(message.parts):
                if isinstance(part, UserPromptPart):
                    return ModelResponse(
                        parts=[
                            ToolCallPart(
                                "acp_set_plan",
                                {
                                    "plan_md": "# Plan\n\n1. Inspect the repo\n2. Write the plan\n",
                                    "entries": [
                                        {
                                            "content": "Inspect the repository structure",
                                            "priority": "high",
                                            "status": "in_progress",
                                        },
                                        {
                                            "content": "Review the implementation constraints",
                                            "priority": "medium",
                                            "status": "pending",
                                        },
                                    ],
                                },
                            )
                        ]
                    )
        raise AssertionError("expected a user prompt")

    adapter = create_acp_agent(
        agent=Agent(
            FunctionModel(route_plan_tool, model_name="native-plan-tool-model"),
            output_type=str,
        ),
        config=AdapterConfig(
            capability_bridges=[
                PrepareToolsBridge(
                    default_mode_id="plan",
                    modes=[
                        PrepareToolsMode(
                            id="plan",
                            name="Plan",
                            description="Native plan mode.",
                            prepare_func=expose_tools,
                            plan_mode=True,
                        )
                    ],
                )
            ],
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    client.updates.clear()

    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Create a plan for this task.")],
            session_id=session.session_id,
        )
    )

    plan_updates = [update for _, update in client.updates if isinstance(update, AgentPlanUpdate)]
    assert len(plan_updates) == 2
    assert [entry.content for entry in plan_updates[-1].entries] == [
        "Inspect the repository structure",
        "Review the implementation constraints",
    ]
    assert [entry.status for entry in plan_updates[-1].entries] == [
        "in_progress",
        "pending",
    ]
    assert agent_message_texts(client) == ["# Plan\n\n1. Inspect the repo\n2. Write the plan\n"]


def test_plan_mode_can_capture_native_plan_generation_output(tmp_path: Path) -> None:
    def expose_tools(
        ctx: RunContext[None],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        del ctx
        return list(tool_defs)

    adapter = create_acp_agent(
        agent=Agent(
            TestModel(
                call_tools=[],
                custom_output_args={
                    "plan_md": "# Native Plan\n\n- Inspect the repo\n- Save the plan\n",
                    "plan_entries": [
                        {
                            "content": "Inspect the repository",
                            "priority": "high",
                            "status": "in_progress",
                        },
                        {
                            "content": "Review the implementation constraints",
                            "priority": "medium",
                            "status": "pending",
                        },
                    ],
                },
                model_name="native-plan-output-model",
            ),
            output_type=str,
        ),
        config=AdapterConfig(
            capability_bridges=[
                PrepareToolsBridge(
                    default_mode_id="plan",
                    modes=[
                        PrepareToolsMode(
                            id="plan",
                            name="Plan",
                            description="Native plan mode.",
                            prepare_func=expose_tools,
                            plan_mode=True,
                        )
                    ],
                )
            ],
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    client.updates.clear()

    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Plan the implementation.")],
            session_id=session.session_id,
        )
    )

    plan_updates = [update for _, update in client.updates if isinstance(update, AgentPlanUpdate)]
    assert len(plan_updates) == 1
    assert [entry.content for entry in plan_updates[0].entries] == [
        "Inspect the repository",
        "Review the implementation constraints",
    ]
    assert agent_message_texts(client) == ["# Native Plan\n\n- Inspect the repo\n- Save the plan\n"]


def test_agent_mode_with_plan_tools_can_update_and_complete_entries_incrementally(
    tmp_path: Path,
) -> None:
    def expose_tools(
        ctx: RunContext[None],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        del ctx
        return list(tool_defs)

    def route_plan_progress(
        messages: list[ModelRequest | ModelResponse],
        info: AgentInfo,
    ) -> ModelResponse:
        del info
        for message in reversed(messages):
            if not isinstance(message, ModelRequest):
                continue
            tool_returns = [part for part in message.parts if isinstance(part, ToolReturnPart)]
            if tool_returns:
                last_return = tool_returns[-1]
                if last_return.tool_name == "acp_set_plan":
                    return ModelResponse(
                        parts=[
                            ToolCallPart(
                                "acp_update_plan_entry",
                                {"index": 1, "status": "in_progress"},
                            )
                        ]
                    )
                if last_return.tool_name == "acp_update_plan_entry":
                    return ModelResponse(parts=[ToolCallPart("acp_mark_plan_done", {"index": 1})])
                if last_return.tool_name == "acp_mark_plan_done":
                    return ModelResponse(parts=[TextPart("Plan progress recorded.")])
            for part in reversed(message.parts):
                if isinstance(part, UserPromptPart):
                    return ModelResponse(
                        parts=[
                            ToolCallPart(
                                "acp_set_plan",
                                {
                                    "plan_md": "# Plan\n\n1. Implement the first item\n2. Verify it\n",
                                    "entries": [
                                        {
                                            "content": "Implement the first item",
                                            "priority": "high",
                                            "status": "pending",
                                        },
                                        {
                                            "content": "Verify the implementation",
                                            "priority": "medium",
                                            "status": "pending",
                                        },
                                    ],
                                },
                            )
                        ]
                    )
        raise AssertionError("expected a user prompt or tool return")

    adapter = create_acp_agent(
        agent=Agent(
            FunctionModel(route_plan_progress, model_name="native-plan-progress-model"),
            output_type=str,
        ),
        config=AdapterConfig(
            capability_bridges=[
                PrepareToolsBridge(
                    default_mode_id="agent",
                    modes=[
                        PrepareToolsMode(
                            id="agent",
                            name="Agent",
                            description="Execution mode with plan progress tools.",
                            prepare_func=expose_tools,
                            plan_tools=True,
                        )
                    ],
                )
            ],
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    client.updates.clear()

    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Implement the first plan item.")],
            session_id=session.session_id,
        )
    )

    plan_updates = [update for _, update in client.updates if isinstance(update, AgentPlanUpdate)]
    assert len(plan_updates) == 3
    assert [entry.status for entry in plan_updates[0].entries] == ["pending", "pending"]
    assert [entry.status for entry in plan_updates[1].entries] == [
        "in_progress",
        "pending",
    ]
    assert [entry.status for entry in plan_updates[2].entries] == [
        "completed",
        "pending",
    ]
    assert agent_message_texts(client) == ["Plan progress recorded."]


def test_provider_backed_updates_drive_prompt_state(tmp_path: Path) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="provider:default")),
        config=AdapterConfig(
            config_options_provider=DemoConfigOptionsProvider(),
            models_provider=DemoModelsProvider(),
            modes_provider=DemoModesProvider(),
            plan_provider=DemoPlanProvider(),
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    first_session = asyncio.run(adapter.new_session(cwd=str(tmp_path / "alpha"), mcp_servers=[]))
    second_session = asyncio.run(adapter.new_session(cwd=str(tmp_path / "beta"), mcp_servers=[]))
    client.updates.clear()

    set_mode_response = asyncio.run(
        adapter.set_session_mode(mode_id="review", session_id=second_session.session_id)
    )
    set_stream_response = asyncio.run(
        adapter.set_config_option(
            config_id="stream_enabled",
            session_id=second_session.session_id,
            value=True,
        )
    )
    asyncio.run(
        adapter.set_session_model(
            model_id="provider-model-b",
            session_id=second_session.session_id,
        )
    )

    assert set_mode_response is not None
    assert set_stream_response is not None
    assert any(
        isinstance(update, CurrentModeUpdate) and update.current_mode_id == "review"
        for _, update in client.updates
    )
    assert any(
        isinstance(update, ConfigOptionUpdate)
        and any(
            option.id == "stream_enabled" and option.current_value is True
            for option in update.config_options
            if isinstance(option, SessionConfigOptionBoolean)
        )
        for _, update in client.updates
    )
    assert any(
        isinstance(update, AgentPlanUpdate)
        and [entry.content for entry in update.entries] == ["mode:review", "stream:true"]
        for _, update in client.updates
    )

    client.updates.clear()
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Use the provider default model.")],
            session_id=first_session.session_id,
        )
    )
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Use the provider override.")],
            session_id=second_session.session_id,
        )
    )

    assert agent_message_texts(client) == ["provider:model-a", "provider:model-b"]
    second_session_updates = [
        update for session_id, update in client.updates if session_id == second_session.session_id
    ]
    assert any(
        isinstance(update, CurrentModeUpdate) and update.current_mode_id == "review"
        for update in second_session_updates
    )
    assert any(
        isinstance(update, AgentPlanUpdate)
        and [entry.content for entry in update.entries] == ["mode:review", "stream:true"]
        for update in second_session_updates
    )


def test_reserved_model_config_option_falls_back_to_provider(tmp_path: Path) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="provider:model-config")),
        config=AdapterConfig(
            config_options_provider=ReservedModelConfigProvider(),
            session_store=MemorySessionStore(),
        ),
    )

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.set_config_option(
            config_id="model",
            session_id=session.session_id,
            value="provider-model-b",
        )
    )

    assert response is not None
    assert response.config_options[0].id == "model"
    assert response.config_options[0].current_value == "provider-model-b"


def test_freeform_model_provider_omits_select_option_and_accepts_custom_ids(
    tmp_path: Path,
) -> None:
    session_store = MemorySessionStore()
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="provider:freeform")),
        config=AdapterConfig(
            models_provider=FreeformModelsProvider(),
            session_store=session_store,
        ),
    )

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.set_session_model(
            model_id="custom-model-z",
            session_id=session.session_id,
        )
    )
    stored_session = session_store.get(session.session_id)

    assert session.config_options is None
    assert session.models is not None
    assert session.models.current_model_id == "custom-model-a"
    assert response is not None
    assert stored_session is not None
    assert stored_session.session_model_id == "custom-model-z"


def test_async_mode_config_and_plan_providers_are_supported(tmp_path: Path) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="provider:async")),
        config=AdapterConfig(
            config_options_provider=AsyncDemoConfigOptionsProvider(),
            models_provider=DemoModelsProvider(),
            modes_provider=AsyncDemoModesProvider(),
            plan_provider=AsyncDemoPlanProvider(),
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    set_mode_response = asyncio.run(
        adapter.set_session_mode(mode_id="review", session_id=session.session_id)
    )
    set_stream_response = asyncio.run(
        adapter.set_config_option(
            config_id="stream_enabled",
            session_id=session.session_id,
            value=True,
        )
    )

    assert set_mode_response is not None
    assert set_stream_response is not None
    assert any(
        isinstance(update, CurrentModeUpdate) and update.current_mode_id == "review"
        for _, update in client.updates
    )
    assert any(
        isinstance(update, AgentPlanUpdate)
        and [entry.content for entry in update.entries] == ["mode:review", "stream:true"]
        for _, update in client.updates
    )


def test_set_session_mode_returns_none_without_mode_support(tmp_path: Path) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="no-modes")),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(adapter.set_session_mode(mode_id="chat", session_id=session.session_id))

    assert response is None


def test_set_session_model_returns_none_without_model_support(tmp_path: Path) -> None:
    session_store = MemorySessionStore()
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="no-models")),
        config=AdapterConfig(session_store=session_store),
    )

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.set_session_model(model_id="openai:gpt-5", session_id=session.session_id)
    )
    stored_session = session_store.get(session.session_id)

    assert response is not None
    assert stored_session is not None
    assert stored_session.session_model_id == "openai:gpt-5"


def test_set_session_model_rejects_unknown_model_id(tmp_path: Path) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="default", model_name="model-a")),
        config=AdapterConfig(
            allow_model_selection=True,
            available_models=[
                AdapterModel(
                    model_id="model-a",
                    name="Model A",
                    override=TestModel(custom_output_text="default", model_name="model-a"),
                )
            ],
            session_store=MemorySessionStore(),
        ),
    )

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    with pytest.raises(RequestError):
        asyncio.run(
            adapter.set_session_model(model_id="missing-model", session_id=session.session_id)
        )


def test_set_config_option_rejects_invalid_model_and_mode_value_types(
    tmp_path: Path,
) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="typed-config")),
        config=AdapterConfig(
            allow_model_selection=True,
            available_models=[AdapterModel(model_id="model-a", name="Model A", override="model-a")],
            modes_provider=DemoModesProvider(),
            session_store=MemorySessionStore(),
        ),
    )

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    with pytest.raises(RequestError):
        asyncio.run(
            adapter.set_config_option(
                config_id="model",
                session_id=session.session_id,
                value=False,
            )
        )
    with pytest.raises(RequestError):
        asyncio.run(
            adapter.set_config_option(
                config_id="mode",
                session_id=session.session_id,
                value=False,
            )
        )


def test_set_config_option_returns_none_for_unknown_option(tmp_path: Path) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="unknown-config")),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    response = asyncio.run(
        adapter.set_config_option(
            config_id="missing_option",
            session_id=session.session_id,
            value=True,
        )
    )

    assert response is None
