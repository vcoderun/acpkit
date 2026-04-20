from __future__ import annotations as _annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, TypeAlias

from acp.schema import PlanEntry
from pydantic import BaseModel

if TYPE_CHECKING:
    from .session.state import AcpSessionContext

PlanGenerationType: TypeAlias = Literal["tools", "structured"]

__all__ = (
    "NativePlanGeneration",
    "PlanGenerationType",
    "TaskPlan",
    "acp_get_plan",
    "acp_mark_plan_done",
    "acp_set_plan",
    "acp_update_plan_entry",
    "native_plan_tools",
)


class TaskPlan(BaseModel):
    plan_md: str
    plan_entries: list[PlanEntry]


NativePlanGeneration = TaskPlan


class _ActivePlanRuntime(Protocol):
    def format_native_plan(self, session: AcpSessionContext) -> str: ...

    async def persist_native_plan_state(
        self,
        session: AcpSessionContext,
        *,
        entries: Sequence[PlanEntry],
        plan_markdown: str | None,
    ) -> None: ...

    async def update_native_plan_entry(
        self,
        session: AcpSessionContext,
        *,
        index: int,
        status: str | None = None,
        content: str | None = None,
        priority: str | None = None,
    ) -> PlanEntry: ...


@dataclass(slots=True, frozen=True)
class _BoundPlanContext:
    runtime: _ActivePlanRuntime
    session: AcpSessionContext


_ACTIVE_PLAN_CONTEXT: ContextVar[_BoundPlanContext | None] = ContextVar(
    "langchain_acp_active_plan_context",
    default=None,
)


@contextmanager
def _bind_native_plan_context(
    runtime: _ActivePlanRuntime,
    session: AcpSessionContext,
) -> Iterator[None]:
    token: Token[_BoundPlanContext | None] = _ACTIVE_PLAN_CONTEXT.set(
        _BoundPlanContext(runtime=runtime, session=session)
    )
    try:
        yield
    finally:
        _ACTIVE_PLAN_CONTEXT.reset(token)


def _active_plan_context() -> _BoundPlanContext | None:
    return _ACTIVE_PLAN_CONTEXT.get()


def acp_get_plan() -> str:
    """Return the saved plan and numbered entries."""
    context = _active_plan_context()
    if context is None:
        return "No active ACP session is bound."
    return context.runtime.format_native_plan(context.session)


async def acp_set_plan(entries: list[PlanEntry], plan_md: str | None = None) -> str:
    """Replace the current ACP-owned plan state."""
    context = _active_plan_context()
    if context is None:
        return "No active ACP session is bound."
    await context.runtime.persist_native_plan_state(
        context.session,
        entries=entries,
        plan_markdown=plan_md,
    )
    return f"Recorded {len(entries)} plan entries."


async def acp_update_plan_entry(
    index: int,
    status: str | None = None,
    content: str | None = None,
    priority: str | None = None,
) -> str:
    """Update a single plan entry by its 1-based index."""
    context = _active_plan_context()
    if context is None:
        return "No active ACP session is bound."
    updated_entry = await context.runtime.update_native_plan_entry(
        context.session,
        index=index,
        status=status,
        content=content,
        priority=priority,
    )
    return (
        f"Updated plan entry {index}: "
        f"[{updated_entry.status}] ({updated_entry.priority}) {updated_entry.content}"
    )


async def acp_mark_plan_done(index: int) -> str:
    """Mark a single plan entry completed by its 1-based index."""
    context = _active_plan_context()
    if context is None:
        return "No active ACP session is bound."
    updated_entry = await context.runtime.update_native_plan_entry(
        context.session,
        index=index,
        status="completed",
    )
    return f"Marked plan entry {index} as completed: {updated_entry.content}"


def native_plan_tools() -> tuple[object, ...]:
    return (
        acp_get_plan,
        acp_set_plan,
        acp_update_plan_entry,
        acp_mark_plan_done,
    )
