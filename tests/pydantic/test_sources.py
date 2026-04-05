from __future__ import annotations as _annotations

import asyncio

import pytest
from acp.exceptions import RequestError

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
    MemorySessionStore,
    Path,
    RecordingClient,
    RunContext,
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
            return None

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
        )
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
        )
    )

    tool_updates = [
        update.raw_output for _, update in client.updates if isinstance(update, ToolCallProgress)
    ]
    assert "deps:9" in tool_updates


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
        )
    )
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Use the second session agent.")],
            session_id=second_session.session_id,
        )
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
        )
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
        )
    )

    assert agent_message_texts(client) == ["async-factory:gamma"]


def test_load_missing_session_returns_none_and_resume_or_fork_raise(
    tmp_path: Path,
) -> None:
    adapter = create_acp_agent(
        agent=Agent(TestModel(custom_output_text="missing-session")),
        config=AdapterConfig(session_store=MemorySessionStore()),
    )

    load_response = asyncio.run(
        adapter.load_session(cwd=str(tmp_path), session_id="missing", mcp_servers=[])
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
        )
    )

    client.updates.clear()
    forked = asyncio.run(
        adapter.fork_session(
            cwd=str(tmp_path / "forked"),
            session_id=original.session_id,
            mcp_servers=[],
        )
    )
    resume_response = asyncio.run(
        adapter.resume_session(
            cwd=str(tmp_path / "forked"),
            session_id=forked.session_id,
            mcp_servers=[],
        )
    )

    assert resume_response.models is not None
    assert resume_response.models.current_model_id == "model-b"
    replayed_update_types = [
        type(update)
        for _, update in client.updates
        if not isinstance(update, AvailableCommandsUpdate)
    ]
    assert replayed_update_types[0] is UserMessageChunk
    assert replayed_update_types[1:] == [AgentMessageChunk] * (len(replayed_update_types) - 1)
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
        )
    )
    asyncio.run(
        adapter.set_session_model(
            model_id="provider-model-b",
            session_id=original.session_id,
        )
    )
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Prime the provider-backed session.")],
            session_id=original.session_id,
        )
    )

    client.updates.clear()
    forked = asyncio.run(
        adapter.fork_session(
            cwd=str(tmp_path / "forked"),
            session_id=original.session_id,
            mcp_servers=[],
        )
    )
    resume_response = asyncio.run(
        adapter.resume_session(
            cwd=str(tmp_path / "forked"),
            session_id=forked.session_id,
            mcp_servers=[],
        )
    )

    assert forked.models is not None
    assert forked.models.current_model_id == "provider-model-b"
    assert forked.modes is not None
    assert forked.modes.current_mode_id == "review"
    assert forked.config_options is not None
    assert [option.id for option in forked.config_options] == [
        "model",
        "mode",
        "stream_enabled",
    ]
    assert resume_response.models is not None
    assert resume_response.models.current_model_id == "provider-model-b"
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
        )
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
        )
    )
    client.updates.clear()

    load_response = asyncio.run(
        adapter.load_session(cwd=str(tmp_path), session_id=session.session_id, mcp_servers=[])
    )

    assert load_response is not None
    assert all(isinstance(update, AvailableCommandsUpdate) for _, update in client.updates)
