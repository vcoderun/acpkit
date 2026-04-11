from __future__ import annotations as _annotations

import asyncio
from typing import Any

from pydantic_ai import ModelRequest, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from .support import (
    UTC,
    AcpSessionContext,
    AdapterConfig,
    Agent,
    MemorySessionStore,
    Path,
    RecordingClient,
    TestModel,
    ThinkingBridge,
    create_acp_agent,
    datetime,
    text_block,
)


def test_thinking_bridge_exposes_config_and_maps_to_model_settings() -> None:
    session = AcpSessionContext(
        session_id="session-thinking",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    agent = Agent(TestModel(custom_output_text="unused"))
    bridge = ThinkingBridge()

    options = bridge.get_config_options(session, agent)
    assert len(options) == 1
    assert options[0].id == "thinking"
    assert options[0].current_value == "default"
    assert bridge.get_model_settings(session, agent) is None

    assert bridge.set_config_option(session, agent, "thinking", True) is None
    assert bridge.set_config_option(session, agent, "thinking", "invalid") is None

    updated = bridge.set_config_option(session, agent, "thinking", "high")
    assert updated is not None
    assert updated[0].current_value == "high"
    assert bridge.get_model_settings(session, agent) == {"thinking": "high"}

    metadata = bridge.get_session_metadata(session, agent)
    assert metadata == {
        "config_id": "thinking",
        "current_value": "high",
        "supported_values": [
            "default",
            "off",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
        ],
    }

    reset = bridge.set_config_option(session, agent, "thinking", "default")
    assert reset is not None
    assert reset[0].current_value == "default"
    assert bridge.get_model_settings(session, agent) is None


def test_thinking_bridge_integration_exposes_ui_option_and_sets_run_effort(
    tmp_path: Path,
) -> None:
    observed_model_settings: list[Any] = []

    def route_with_thinking(
        messages: list[ModelRequest | ModelResponse],
        info: AgentInfo,
    ) -> ModelResponse:
        del messages
        observed_model_settings.append(info.model_settings)
        return ModelResponse(parts=[TextPart("done")])

    adapter = create_acp_agent(
        agent=Agent(
            FunctionModel(route_with_thinking, model_name="thinking-model"),
            output_type=str,
        ),
        config=AdapterConfig(
            capability_bridges=[ThinkingBridge()],
            session_store=MemorySessionStore(),
        ),
    )
    client = RecordingClient()
    adapter.on_connect(client)

    session = asyncio.run(adapter.new_session(cwd=str(tmp_path), mcp_servers=[]))
    assert session.config_options is not None
    session_options = {option.id: option for option in session.config_options}
    assert "thinking" in session_options
    assert session_options["thinking"].current_value == "default"

    update_response = asyncio.run(
        adapter.set_config_option(
            config_id="thinking",
            session_id=session.session_id,
            value="high",
        )
    )
    assert update_response is not None
    assert update_response.config_options is not None
    updated_options = {option.id: option for option in update_response.config_options}
    assert updated_options["thinking"].current_value == "high"

    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Use the selected thinking effort.")],
            session_id=session.session_id,
        )
    )
    assert observed_model_settings[-1] == {"thinking": "high"}

    asyncio.run(
        adapter.set_config_option(
            config_id="thinking",
            session_id=session.session_id,
            value="off",
        )
    )
    asyncio.run(
        adapter.prompt(
            prompt=[text_block("Disable thinking for this run.")],
            session_id=session.session_id,
        )
    )
    assert observed_model_settings[-1] == {"thinking": False}
