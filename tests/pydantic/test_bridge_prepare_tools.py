from __future__ import annotations as _annotations

import asyncio
from typing import Any, cast

import pytest

from .support import (
    UTC,
    AcpSessionContext,
    Agent,
    Path,
    PrepareToolsBridge,
    PrepareToolsMode,
    RunContext,
    SessionConfigOptionSelect,
    TestModel,
    ToolDefinition,
    datetime,
)


def _passthrough_tools(
    ctx: RunContext[None],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    del ctx
    return list(tool_defs)


def test_passthrough_tools_helper_returns_a_copy() -> None:
    tool_defs: list[ToolDefinition] = []
    copied = _passthrough_tools(cast(Any, None), tool_defs)
    assert copied == []
    assert copied is not tool_defs


def test_prepare_tools_bridge_allows_at_most_one_plan_mode() -> None:
    with pytest.raises(ValueError, match="at most one `plan_mode=True`"):
        PrepareToolsBridge(
            default_mode_id="chat",
            modes=[
                PrepareToolsMode(
                    id="chat",
                    name="Chat",
                    prepare_func=_passthrough_tools,
                    plan_mode=True,
                ),
                PrepareToolsMode(
                    id="plan",
                    name="Plan",
                    prepare_func=_passthrough_tools,
                    plan_mode=True,
                ),
            ],
        )


def test_prepare_tools_bridge_rejects_invalid_default_plan_generation_type() -> None:
    with pytest.raises(ValueError, match="default plan generation type"):
        PrepareToolsBridge(
            default_mode_id="plan",
            modes=[
                PrepareToolsMode(
                    id="plan",
                    name="Plan",
                    prepare_func=_passthrough_tools,
                    plan_mode=True,
                )
            ],
            default_plan_generation_type=cast(Any, "invalid"),
        )


def test_prepare_tools_bridge_can_enable_plan_tools_outside_plan_mode() -> None:
    bridge = PrepareToolsBridge(
        default_mode_id="agent",
        modes=[
            PrepareToolsMode(
                id="plan",
                name="Plan",
                prepare_func=_passthrough_tools,
                plan_mode=True,
            ),
            PrepareToolsMode(
                id="agent",
                name="Agent",
                prepare_func=_passthrough_tools,
                plan_tools=True,
            ),
        ],
    )

    session = AcpSessionContext(
        session_id="plan-tools",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        config_values={"mode": "agent"},
    )

    assert bridge.is_plan_mode(session) is False
    assert bridge.supports_plan_tools(session) is True
    assert bridge.supports_plan_write_tools(session) is True
    assert bridge.supports_plan_progress(session) is True
    config_options = bridge.get_config_options(session, Agent(TestModel()))
    assert len(config_options) == 1
    assert config_options[0].id == "plan_generation_type"
    assert (
        bridge.set_config_option(
            session,
            Agent(TestModel()),
            "plan_generation_type",
            "tools",
        )
        is not None
    )


def test_prepare_tools_bridge_exposes_plan_generation_config_and_helpers() -> None:
    bridge = PrepareToolsBridge(
        default_mode_id="plan",
        modes=[
            PrepareToolsMode(
                id="plan",
                name="Plan",
                prepare_func=_passthrough_tools,
                plan_mode=True,
            ),
            PrepareToolsMode(
                id="agent",
                name="Agent",
                prepare_func=_passthrough_tools,
                plan_tools=True,
            ),
        ],
    )
    session = AcpSessionContext(
        session_id="plan-generation",
        cwd=Path("/tmp"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    agent = Agent(TestModel())

    config_options = bridge.get_config_options(session, agent)
    assert len(config_options) == 1
    assert isinstance(config_options[0], SessionConfigOptionSelect)
    assert config_options[0].id == "plan_generation_type"
    assert config_options[0].current_value == "structured"
    assert bridge.uses_structured_plan_generation(session) is True
    assert bridge.supports_plan_write_tools(session) is False
    metadata = bridge.get_session_metadata(session, agent)
    assert metadata["current_plan_generation_type"] == "structured"
    assert metadata["supported_plan_generation_types"] == ["tools", "structured"]

    updated = bridge.set_config_option(session, agent, "plan_generation_type", "tools")
    assert updated is not None
    assert bridge.current_plan_generation_type(session) == "tools"
    assert bridge.uses_tool_plan_generation(session) is True
    assert bridge.supports_plan_write_tools(session) is True
    reset = bridge.set_config_option(session, agent, "plan_generation_type", "structured")
    assert reset is not None
    assert bridge.current_plan_generation_type(session) == "structured"
    assert "plan_generation_type" not in session.config_values

    session.config_values["mode"] = "agent"
    assert bridge.supports_plan_progress(session) is True
    assert bridge.supports_plan_write_tools(session) is True
    assert bridge.set_config_option(session, agent, "plan_generation_type", True) is None
    assert bridge.set_config_option(session, agent, "plan_generation_type", "invalid") is None
    session.config_values["plan_generation_type"] = "invalid"
    assert bridge.current_plan_generation_type(session) == "structured"


def test_prepare_tools_bridge_records_failure_events(tmp_path: Path) -> None:
    def boom(
        ctx: RunContext[None],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        del ctx, tool_defs
        raise RuntimeError("boom")

    bridge = PrepareToolsBridge(
        default_mode_id="plan",
        modes=[
            PrepareToolsMode(
                id="plan",
                name="Plan",
                prepare_func=boom,
                plan_mode=True,
            )
        ],
    )
    session = AcpSessionContext(
        session_id="prepare-tools-failure",
        cwd=tmp_path,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    async def run_prepare() -> None:
        prepared = bridge.build_prepare_tools(session)
        result = prepared(cast(Any, None), [])
        if asyncio.iscoroutine(result):
            await result
            return  # pragma: no cover
        assert result == []  # pragma: no cover

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(run_prepare())

    updates = bridge.drain_updates(session, Agent(TestModel()))
    assert updates is not None


def test_prepare_tools_bridge_rejects_reserved_mode_ids() -> None:
    with pytest.raises(ValueError, match="reserved slash command names"):
        PrepareToolsBridge(
            default_mode_id="model",
            modes=[
                PrepareToolsMode(
                    id="model",
                    name="Model",
                    prepare_func=_passthrough_tools,
                )
            ],
        )
