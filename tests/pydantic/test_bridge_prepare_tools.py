from __future__ import annotations as _annotations

import pytest

from .support import (
    UTC,
    AcpSessionContext,
    Path,
    PrepareToolsBridge,
    PrepareToolsMode,
    RunContext,
    ToolDefinition,
    datetime,
)


def test_prepare_tools_bridge_allows_at_most_one_plan_mode() -> None:
    def passthrough(
        ctx: RunContext[None],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        del ctx
        return list(tool_defs)

    with pytest.raises(ValueError, match="at most one `plan_mode=True`"):
        PrepareToolsBridge(
            default_mode_id="chat",
            modes=[
                PrepareToolsMode(
                    id="chat",
                    name="Chat",
                    prepare_func=passthrough,
                    plan_mode=True,
                ),
                PrepareToolsMode(
                    id="plan",
                    name="Plan",
                    prepare_func=passthrough,
                    plan_mode=True,
                ),
            ],
        )


def test_prepare_tools_bridge_can_enable_plan_tools_outside_plan_mode() -> None:
    def passthrough(
        ctx: RunContext[None],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        del ctx
        return list(tool_defs)

    bridge = PrepareToolsBridge(
        default_mode_id="agent",
        modes=[
            PrepareToolsMode(
                id="plan",
                name="Plan",
                prepare_func=passthrough,
                plan_mode=True,
            ),
            PrepareToolsMode(
                id="agent",
                name="Agent",
                prepare_func=passthrough,
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


def test_prepare_tools_bridge_rejects_reserved_mode_ids() -> None:
    def passthrough(
        ctx: RunContext[None],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        del ctx
        return list(tool_defs)

    with pytest.raises(ValueError, match="reserved slash command names"):
        PrepareToolsBridge(
            default_mode_id="model",
            modes=[
                PrepareToolsMode(
                    id="model",
                    name="Model",
                    prepare_func=passthrough,
                )
            ],
        )
