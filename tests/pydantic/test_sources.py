from __future__ import annotations as _annotations

import asyncio
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager

import pytest
from acp.exceptions import RequestError
from pydantic_ai import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.tools import DeferredToolRequests, ToolApproved

from .support import (
    AcpSessionContext,
    AdapterConfig,
    AdapterModel,
    Agent,
    AgentMessageChunk,
    AgentPlanUpdate,
    AvailableCommandsUpdate,
    DemoConfigOptionsProvider,
    DemoModelsProvider,
    DemoModesProvider,
    DemoPlanProvider,
    JsonValue,
    MemorySessionStore,
    Path,
    RecordingClient,
    RunContext,
    SessionInfoUpdate,
    TestModel,
    ToolCallProgress,
    UserMessageChunk,
    agent_message_texts,
    create_acp_agent,
    text_block,
)


def test_custom_agent_source_is_supported(tmp_path: Path) -> None:
    class SessionAwareAgentSource:
        async def get_agent(self, session: AcpSessionContext) -> Agent[None, str]:
            return Agent(TestModel(custom_output_text=f"source:{session.cwd.name}"))

        async def get_deps(
            self,
            session: AcpSessionContext,
            agent: Agent[None, str],
        ) -> None:
            del session, agent

    adapter = create_acp_agent(
        agent_source=SessionAwareAgentSource(),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path / "source-demo"), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Use the custom agent source.")],
            session_id=session.session_id,
        ),
    )

    assert agent_message_texts(client) == ["source:source-demo"]


def test_custom_agent_source_can_supply_session_deps(tmp_path: Path) -> None:
    class SessionAwareDepsSource:
        async def get_agent(self, session: AcpSessionContext) -> Agent[int, str]:
            del session
            agent = Agent[int, str](
                TestModel(call_tools=["show_deps"], custom_output_text="deps-complete"),
                deps_type=int,
            )

            @agent.tool
            def show_deps(ctx: RunContext[int]) -> str:
                return f"deps:{ctx.deps}"

            return agent

        async def get_deps(
            self,
            session: AcpSessionContext,
            agent: Agent[int, str],
        ) -> int:
            del agent
            return len(session.cwd.name)

    adapter = create_acp_agent(
        agent_source=SessionAwareDepsSource(),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path / "deps-demo"), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Show the deps value.")],
            session_id=session.session_id,
        ),
    )

    tool_updates = [
        update.raw_output for _, update in client.updates if isinstance(update, ToolCallProgress)
    ]
    assert "deps:9" in tool_updates


def test_connected_client_is_available_to_sources_and_providers(tmp_path: Path) -> None:
    client = RecordingClient()

    class ClientAwareApprovalProvider:
        def get_approval_state(
            self,
            session: AcpSessionContext,
            agent: Agent[None, str],
        ) -> dict[str, JsonValue]:
            del agent
            return {"connected": session.client is client}

    class ClientAwareSource:
        async def get_agent(self, session: AcpSessionContext) -> Agent[None, str]:
            output_text = "client:connected" if session.client is client else "client:missing"
            return Agent(TestModel(custom_output_text=output_text))

        async def get_deps(
            self,
            session: AcpSessionContext,
            agent: Agent[None, str],
        ) -> None:
            del session, agent

    adapter = create_acp_agent(
        agent_source=ClientAwareSource(),
        config=AdapterConfig(
            approval_state_provider=ClientAwareApprovalProvider(),
            session_store=MemorySessionStore(),
        ),
    )
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path / "client-aware"), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Use the client-aware agent source.")],
            session_id=session.session_id,
        ),
    )

    session_info_updates = [
        update for _, update in client.updates if isinstance(update, SessionInfoUpdate)
    ]
    assert session_info_updates
    assert session_info_updates[-1].field_meta == {
        "pydantic_acp": {"approval_state": {"connected": True}},
    }
    assert agent_message_texts(client) == ["client:connected"]


def test_agent_factory_builds_session_specific_agents(tmp_path: Path) -> None:
    def factory(session: AcpSessionContext) -> Agent[None, str]:
        return Agent(TestModel(custom_output_text=f"factory:{session.cwd.name}"))

    adapter = create_acp_agent(
        agent_factory=factory,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    first_session = asyncio.run(adapter.new_session(cwd=str(tmp_path / "alpha"), mcp_servers=[]))
    second_session = asyncio.run(adapter.new_session(cwd=str(tmp_path / "beta"), mcp_servers=[]))

    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Use the first session agent.")],
            session_id=first_session.session_id,
        ),
    )
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Use the second session agent.")],
            session_id=second_session.session_id,
        ),
    )

    assert agent_message_texts(client) == ["factory:alpha", "factory:beta"]


def test_factory_receives_updated_session_state(tmp_path: Path) -> None:
    def factory(session: AcpSessionContext) -> Agent[None, str]:
        current_flag = str(session.config_values.get("demo_flag", "unset"))
        current_tag = str(session.metadata.get("demo_tag", "missing"))
        return Agent(TestModel(custom_output_text=f"factory:{current_flag}:{current_tag}"))

    session_store = MemorySessionStore()
    adapter = create_acp_agent(
        agent_factory=factory,
        config=AdapterConfig(session_store=session_store),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path / "stateful"), mcp_servers=[]))
    stored_session = session_store.get(session.session_id)
    assert stored_session is not None
    stored_session.config_values["demo_flag"] = "enabled"
    stored_session.metadata["demo_tag"] = "from-store"
    session_store.save(stored_session)
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Show the current session-aware factory state.")],
            session_id=session.session_id,
        ),
    )

    assert agent_message_texts(client) == ["factory:enabled:from-store"]


def test_async_agent_factory_is_supported(tmp_path: Path) -> None:
    async def factory(session: AcpSessionContext) -> Agent[None, str]:
        return Agent(TestModel(custom_output_text=f"async-factory:{session.cwd.name}"))

    adapter = create_acp_agent(
        agent_factory=factory,
        config=AdapterConfig(session_store=MemorySessionStore()),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path / "gamma"), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Use the async factory agent.")],
            session_id=session.session_id,
        ),
    )

    assert agent_message_texts(client) == ["async-factory:gamma"]


def test_agent_source_can_own_prompt_scope_history_and_session_close(tmp_path: Path) -> None:
    scope_active = False
    observed_messages: list[list[ModelMessage]] = []
    persisted_history_sizes: list[int] = []
    closed_sessions: list[str] = []

    async def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        assert scope_active
        observed_messages.append(list(messages))
        return ModelResponse(parts=[TextPart(f"response-{len(observed_messages)}")])

    class StatefulSource:
        def __init__(self) -> None:
            self.agent = Agent(FunctionModel(respond))

        async def get_agent(self, session: AcpSessionContext) -> Agent[None, str]:
            del session
            return self.agent

        async def get_deps(
            self,
            session: AcpSessionContext,
            agent: Agent[None, str],
        ) -> None:
            del session, agent

        @contextmanager
        def prompt_run_scope(
            self,
            session: AcpSessionContext,
            agent: Agent[None, str],
        ) -> Iterator[None]:
            nonlocal scope_active
            del session, agent
            assert not scope_active
            scope_active = True
            try:
                yield
            finally:
                scope_active = False

        def get_message_history(
            self,
            session: AcpSessionContext,
            agent: Agent[None, str],
            persisted_history: Sequence[ModelMessage],
        ) -> None:
            del session, agent
            persisted_history_sizes.append(len(persisted_history))

        async def close_session(self, session: AcpSessionContext) -> None:
            closed_sessions.append(session.session_id)

    source = StatefulSource()
    store = MemorySessionStore()
    adapter = create_acp_agent(
        agent_source=source,
        config=AdapterConfig(session_store=store),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("first prompt")],
            session_id=session.session_id,
        )
    )
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("second prompt")],
            session_id=session.session_id,
        )
    )
    asyncio.run(adapter.close_session(session_id=session.session_id))

    assert scope_active is False
    assert persisted_history_sizes[0] == 0
    assert persisted_history_sizes[1] > 0
    assert len(observed_messages) == 2
    assert "first prompt" not in repr(observed_messages[1])
    assert closed_sessions == [session.session_id]
    assert store.get(session.session_id) is None


def test_agent_source_can_emit_persisted_session_updates(tmp_path: Path) -> None:
    class EventSource:
        def __init__(self) -> None:
            self.agent = Agent(TestModel(custom_output_text="event-complete"))

        async def get_agent(self, session: AcpSessionContext) -> Agent[None, str]:
            del session
            return self.agent

        async def get_deps(
            self,
            session: AcpSessionContext,
            agent: Agent[None, str],
        ) -> None:
            del session, agent

        @asynccontextmanager
        async def prompt_run_scope(
            self,
            session: AcpSessionContext,
            agent: Agent[None, str],
        ) -> AsyncIterator[None]:
            del agent
            await session.emit_update(
                ToolCallProgress(
                    session_update="tool_call_update",
                    tool_call_id="source-event-1",
                    title="Source event",
                    kind="think",
                    status="in_progress",
                )
            )
            yield

    source = EventSource()
    store = MemorySessionStore()
    adapter = create_acp_agent(
        agent_source=source,
        config=AdapterConfig(session_store=store),
    )
    client = RecordingClient()
    adapter.on_connect(client)
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    asyncio.run(
        adapter.prompt(
            prompt=[text_block("emit one source event")],
            session_id=session.session_id,
        )
    )

    client_events = [
        update
        for _, update in client.updates
        if isinstance(update, ToolCallProgress) and update.tool_call_id == "source-event-1"
    ]
    stored_session = store.get(session.session_id)
    assert len(client_events) == 1
    assert stored_session is not None
    assert any(
        update.to_update().tool_call_id == "source-event-1"
        for update in stored_session.transcript
        if isinstance(update.to_update(), ToolCallProgress)
    )


def test_agent_source_can_resolve_nested_approval_with_session_policy(tmp_path: Path) -> None:
    approval_results: list[object] = []

    class ApprovalSource:
        def __init__(self) -> None:
            self.agent = Agent(TestModel(custom_output_text="approval-complete"))

        async def get_agent(self, session: AcpSessionContext) -> Agent[None, str]:
            del session
            return self.agent

        async def get_deps(
            self,
            session: AcpSessionContext,
            agent: Agent[None, str],
        ) -> None:
            del session, agent

        @asynccontextmanager
        async def prompt_run_scope(
            self,
            session: AcpSessionContext,
            agent: Agent[None, str],
        ) -> AsyncIterator[None]:
            del agent
            requests = DeferredToolRequests(
                approvals=[
                    ToolCallPart(
                        "write_file",
                        {"path": "nested.txt", "content": "nested"},
                        tool_call_id="nested-write-1",
                    )
                ]
            )
            resolution = await session.resolve_deferred_approvals(requests)
            approval_results.append(resolution.deferred_tool_results.approvals["nested-write-1"])
            yield

    source = ApprovalSource()
    store = MemorySessionStore()
    adapter = create_acp_agent(
        agent_source=source,
        config=AdapterConfig(session_store=store),
    )
    client = RecordingClient()
    client.queue_permission_selected("allow_once")
    adapter.on_connect(client)
    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))

    response = asyncio.run(
        adapter.prompt(
            prompt=[text_block("Resolve a nested approval.")],
            session_id=session.session_id,
        )
    )

    assert response.stop_reason == "end_turn"
    assert len(approval_results) == 1
    assert isinstance(approval_results[0], ToolApproved)
    assert client.permission_option_ids[0][0] == session.session_id
    assert client.permission_option_ids[0][1] == ["allow_once", "reject_once"]
    assert client.permission_option_ids[0][2].tool_call_id == "nested-write-1"


def test_prompts_for_one_session_are_serialized_before_source_lookup(tmp_path: Path) -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()
    observed_messages: list[list[ModelMessage]] = []
    active_scopes = 0
    max_active_scopes = 0

    async def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        observed_messages.append(list(messages))
        if len(observed_messages) == 1:
            first_started.set()
            await release_first.wait()
            return ModelResponse(parts=[TextPart("first-complete")])
        second_started.set()
        return ModelResponse(parts=[TextPart("second-complete")])

    class SerializedSource:
        def __init__(self) -> None:
            self.agent = Agent(FunctionModel(respond))

        async def get_agent(self, session: AcpSessionContext) -> Agent[None, str]:
            del session
            return self.agent

        async def get_deps(
            self,
            session: AcpSessionContext,
            agent: Agent[None, str],
        ) -> None:
            del session, agent

        @asynccontextmanager
        async def prompt_run_scope(
            self,
            session: AcpSessionContext,
            agent: Agent[None, str],
        ) -> AsyncIterator[None]:
            nonlocal active_scopes, max_active_scopes
            del session, agent
            active_scopes += 1
            max_active_scopes = max(max_active_scopes, active_scopes)
            try:
                yield
            finally:
                active_scopes -= 1

    async def scenario() -> None:
        adapter = create_acp_agent(
            agent_source=SerializedSource(),
            config=AdapterConfig(session_store=MemorySessionStore()),
        )
        client = RecordingClient()
        adapter.on_connect(client)
        session = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])

        first = asyncio.create_task(
            adapter.prompt(
                prompt=[text_block("first prompt")],
                session_id=session.session_id,
            )
        )
        await first_started.wait()
        second = asyncio.create_task(
            adapter.prompt(
                prompt=[text_block("second prompt")],
                session_id=session.session_id,
            )
        )
        await asyncio.sleep(0)
        assert not second_started.is_set()
        release_first.set()
        first_response, second_response = await asyncio.gather(first, second)

        assert first_response.stop_reason == "end_turn"
        assert second_response.stop_reason == "end_turn"

    asyncio.run(scenario())

    assert max_active_scopes == 1
    assert len(observed_messages) == 2
    assert "first prompt" in repr(observed_messages[1])


def test_close_cancels_active_prompt_and_rejects_queued_prompt(tmp_path: Path) -> None:
    first_started = asyncio.Event()
    source_closed = asyncio.Event()
    model_calls = 0

    async def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        nonlocal model_calls
        del messages
        model_calls += 1
        first_started.set()
        await asyncio.Event().wait()
        raise AssertionError("cancelled model run resumed unexpectedly")

    class ClosingSource:
        def __init__(self) -> None:
            self.agent = Agent(FunctionModel(respond))

        async def get_agent(self, session: AcpSessionContext) -> Agent[None, str]:
            del session
            return self.agent

        async def get_deps(
            self,
            session: AcpSessionContext,
            agent: Agent[None, str],
        ) -> None:
            del session, agent

        async def close_session(self, session: AcpSessionContext) -> None:
            del session
            source_closed.set()

    async def scenario() -> None:
        store = MemorySessionStore()
        adapter = create_acp_agent(
            agent_source=ClosingSource(),
            config=AdapterConfig(session_store=store),
        )
        session = await adapter.new_session(cwd=str(tmp_path), mcp_servers=[])
        first = asyncio.create_task(
            adapter.prompt(
                prompt=[text_block("first prompt")],
                session_id=session.session_id,
            )
        )
        await first_started.wait()
        second = asyncio.create_task(
            adapter.prompt(
                prompt=[text_block("queued prompt")],
                session_id=session.session_id,
            )
        )
        await asyncio.sleep(0)

        close_response = await adapter.close_session(session_id=session.session_id)
        first_response = await first

        assert close_response is not None
        assert first_response.stop_reason == "cancelled"
        with pytest.raises(RequestError):
            await second
        assert source_closed.is_set()
        assert model_calls == 1
        assert store.get(session.session_id) is None
        assert session.session_id not in adapter._prompt_locks

    asyncio.run(scenario())


def test_load_missing_session_returns_none_and_resume_or_fork_raise(
    tmp_path: Path,
) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="missing-session")),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )

    load_response = asyncio.run(
        adapter.load_session(cwd=str(tmp_path), session_id="missing", mcp_servers=[]),
    )
    close_response = asyncio.run(adapter.close_session(session_id="missing"))

    assert load_response is None
    assert close_response is None
    with pytest.raises(RequestError):
        asyncio.run(adapter.resume_session(cwd=str(tmp_path), session_id="missing", mcp_servers=[]))
    with pytest.raises(RequestError):
        asyncio.run(adapter.fork_session(cwd=str(tmp_path), session_id="missing", mcp_servers=[]))


def test_fork_session_clones_transcript_and_model_override(tmp_path: Path) -> None:
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

    original = asyncio.run(adapter.new_session(cwd=str(tmp_path / "original"), mcp_servers=[]))
    asyncio.run(adapter.set_session_model(model_id="model-b", session_id=original.session_id))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Prime the original session.")],
            session_id=original.session_id,
        ),
    )

    client.updates.clear()
    forked = asyncio.run(
        adapter.fork_session(
            cwd=str(tmp_path / "forked"),
            session_id=original.session_id,
            mcp_servers=[],
        ),
    )
    resume_response = asyncio.run(
        adapter.resume_session(
            cwd=str(tmp_path / "forked"),
            session_id=forked.session_id,
            mcp_servers=[],
        ),
    )

    assert resume_response.config_options is not None
    assert resume_response.config_options[0].current_value == "model-b"
    replayed_update_types = [
        type(update)
        for _, update in client.updates
        if not isinstance(update, AvailableCommandsUpdate | SessionInfoUpdate)
    ]
    assert replayed_update_types[0] is UserMessageChunk
    assert replayed_update_types[1:] == [AgentMessageChunk] * (len(replayed_update_types) - 1)
    assert any(
        isinstance(update, SessionInfoUpdate) and update.title == "Prime the original session."
        for _, update in client.updates
    )
    assert agent_message_texts(client) == ["switched"]


def test_provider_backed_fork_preserves_session_state(tmp_path: Path) -> None:
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

    original = asyncio.run(adapter.new_session(cwd=str(tmp_path / "original"), mcp_servers=[]))
    asyncio.run(adapter.set_session_mode(mode_id="review", session_id=original.session_id))
    asyncio.run(
        adapter.set_config_option(
            config_id="stream_enabled",
            session_id=original.session_id,
            value=True,
        ),
    )
    asyncio.run(
        adapter.set_session_model(
            model_id="provider-model-b",
            session_id=original.session_id,
        ),
    )
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Prime the provider-backed session.")],
            session_id=original.session_id,
        ),
    )

    client.updates.clear()
    forked = asyncio.run(
        adapter.fork_session(
            cwd=str(tmp_path / "forked"),
            session_id=original.session_id,
            mcp_servers=[],
        ),
    )
    resume_response = asyncio.run(
        adapter.resume_session(
            cwd=str(tmp_path / "forked"),
            session_id=forked.session_id,
            mcp_servers=[],
        ),
    )

    assert forked.modes is not None
    assert forked.modes.current_mode_id == "review"
    assert forked.config_options is not None
    assert [option.id for option in forked.config_options] == [
        "model",
        "mode",
        "stream_enabled",
    ]
    assert resume_response.config_options is not None
    assert resume_response.config_options[0].current_value == "provider-model-b"
    assert resume_response.modes is not None
    assert resume_response.modes.current_mode_id == "review"
    resumed_plan_updates = [
        update for _, update in client.updates if isinstance(update, AgentPlanUpdate)
    ]
    assert resumed_plan_updates
    assert [entry.content for entry in resumed_plan_updates[-1].entries] == [
        "mode:review",
        "stream:true",
    ]


def test_close_session_removes_session_from_listing(tmp_path: Path) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="done")),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    listed_before_close = asyncio.run(adapter.list_sessions())
    close_response = asyncio.run(adapter.close_session(session_id=session.session_id))
    listed_after_close = asyncio.run(adapter.list_sessions())
    load_after_close = asyncio.run(
        adapter.load_session(
            cwd=str(tmp_path),
            session_id=session.session_id,
            mcp_servers=[],
        ),
    )

    assert close_response is not None
    assert [item.session_id for item in listed_before_close.sessions] == [session.session_id]
    assert listed_after_close.sessions == []
    assert load_after_close is None


def test_load_session_can_skip_history_replay(tmp_path: Path) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="no-replay")),
        config=AdapterConfig(
            replay_history_on_load=False,
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Prime the transcript without replay.")],
            session_id=session.session_id,
        ),
    )
    client.updates.clear()

    load_response = asyncio.run(
        adapter.load_session(cwd=str(tmp_path), session_id=session.session_id, mcp_servers=[]),
    )

    assert load_response is not None
    assert all(
        isinstance(update, AvailableCommandsUpdate | SessionInfoUpdate)
        for _, update in client.updates
    )
    assert any(
        isinstance(update, SessionInfoUpdate)
        and update.title == "Prime the transcript without replay."
        for _, update in client.updates
    )
