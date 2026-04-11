from __future__ import annotations as _annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from acp.exceptions import RequestError
from acp.schema import AgentPlanUpdate, PlanEntry
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.tools import ToolDefinition

from ..awaitables import resolve_value
from ..bridges import PrepareToolsBridge
from ..session.state import AcpSessionContext, utc_now
from ._agent_state import has_native_plan_tools, set_native_plan_tools_installed, try_active_session

if TYPE_CHECKING:
    from .adapter import PydanticAcpAgent

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)
OutputDataT = TypeVar("OutputDataT", covariant=True)

_GET_PLAN_TOOL_NAME = "acp_get_plan"
_SET_PLAN_TOOL_NAME = "acp_set_plan"
_UPDATE_PLAN_ENTRY_TOOL_NAME = "acp_update_plan_entry"
_MARK_PLAN_DONE_TOOL_NAME = "acp_mark_plan_done"

__all__ = ("_NativePlanRuntime",)


class _NativePlanRuntime(Generic[AgentDepsT, OutputDataT]):
    def __init__(self, owner: PydanticAcpAgent[AgentDepsT, OutputDataT]) -> None:
        self._owner = owner
        self._native_plan_updates: set[str] = set()

    def native_plan_bridge(
        self,
        session: AcpSessionContext,
    ) -> PrepareToolsBridge[Any] | None:
        for bridge in self._owner._config.capability_bridges:
            if isinstance(bridge, PrepareToolsBridge) and bridge.supports_plan_tools(session):
                return bridge
        return None

    def supports_native_plan_state(self, session: AcpSessionContext) -> bool:
        return (
            self._owner._config.plan_provider is None
            and self.native_plan_bridge(session) is not None
        )

    def requires_native_plan_output(self, session: AcpSessionContext) -> bool:
        bridge = self.native_plan_bridge(session)
        if bridge is None:
            return False
        return bridge.is_plan_mode(session)

    def supports_native_plan_progress(self, session: AcpSessionContext) -> bool:
        bridge = self.native_plan_bridge(session)
        if bridge is None:
            return False
        return bridge.current_mode(session).plan_tools

    def get_native_plan_entries(self, session: AcpSessionContext) -> list[PlanEntry] | None:
        if not session.plan_entries:
            return None
        return [PlanEntry.model_validate(entry) for entry in session.plan_entries]

    def set_native_plan_state(
        self,
        session: AcpSessionContext,
        *,
        entries: Sequence[PlanEntry],
        plan_markdown: str | None,
    ) -> None:
        session.plan_entries = [
            entry.model_dump(mode="json", exclude_none=True) for entry in entries
        ]
        session.plan_markdown = plan_markdown

    async def persist_external_plan_state(
        self,
        session: AcpSessionContext,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        entries: Sequence[PlanEntry],
        plan_markdown: str | None,
    ) -> None:
        persistence_provider = self._owner._config.native_plan_persistence_provider
        if persistence_provider is None:
            return
        await resolve_value(
            persistence_provider.persist_plan_state(
                session,
                agent,
                entries,
                plan_markdown,
            )
        )

    async def emit_native_plan_update(self, session: AcpSessionContext) -> None:
        client = self._owner._client
        if client is None:
            return
        entries = self.get_native_plan_entries(session)
        if entries is None:
            return
        self._native_plan_updates.add(session.session_id)
        await client.session_update(
            session_id=session.session_id,
            update=AgentPlanUpdate(session_update="plan", entries=entries),
        )

    def consume_native_plan_update(self, session: AcpSessionContext) -> bool:
        if session.session_id not in self._native_plan_updates:
            return False
        self._native_plan_updates.remove(session.session_id)
        return True

    async def persist_native_plan_state(
        self,
        session: AcpSessionContext,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        entries: Sequence[PlanEntry],
        plan_markdown: str | None,
    ) -> None:
        self.set_native_plan_state(
            session,
            entries=entries,
            plan_markdown=plan_markdown,
        )
        await self.persist_external_plan_state(
            session,
            agent=agent,
            entries=entries,
            plan_markdown=plan_markdown,
        )
        session.updated_at = utc_now()
        self._owner._config.session_store.save(session)
        await self.emit_native_plan_update(session)

    async def persist_current_native_plan_state(
        self,
        session: AcpSessionContext,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        entries = self.get_native_plan_entries(session)
        if entries is None and session.plan_markdown is None:
            return
        await self.persist_native_plan_state(
            session,
            agent=agent,
            entries=() if entries is None else entries,
            plan_markdown=session.plan_markdown,
        )

    def replace_native_plan_entry(
        self,
        session: AcpSessionContext,
        *,
        index: int,
        status: str | None = None,
        content: str | None = None,
        priority: str | None = None,
    ) -> PlanEntry:
        entries = self.get_native_plan_entries(session)
        if not entries:
            raise RequestError.invalid_params({"plan": "No plan entries have been recorded yet."})
        if index < 1 or index > len(entries):
            raise RequestError.invalid_params(
                {
                    "index": index,
                    "plan": f"Plan entry index must be between 1 and {len(entries)}.",
                }
            )
        existing_entry = entries[index - 1]
        updated_payload = existing_entry.model_dump(mode="python")
        if status is not None:
            updated_payload["status"] = status
        if content is not None:
            updated_payload["content"] = content
        if priority is not None:
            updated_payload["priority"] = priority
        updated_entry = PlanEntry.model_validate(updated_payload)
        entries[index - 1] = updated_entry
        self.set_native_plan_state(
            session,
            entries=entries,
            plan_markdown=session.plan_markdown,
        )
        return updated_entry

    def format_native_plan(self, session: AcpSessionContext) -> str:
        entries = self.get_native_plan_entries(session)
        if not entries:
            if session.plan_markdown:
                return session.plan_markdown
            return "No plan has been recorded yet."
        numbered_entries = "\n".join(
            f"{index}. [{entry.status}] ({entry.priority}) {entry.content}"
            for index, entry in enumerate(entries, start=1)
        )
        index_guidance = (
            "Use these 1-based entry numbers with "
            f"`{_UPDATE_PLAN_ENTRY_TOOL_NAME}` and `{_MARK_PLAN_DONE_TOOL_NAME}`."
        )
        if not session.plan_markdown:
            return "\n\n".join((index_guidance, numbered_entries))
        return "\n\n".join(
            (
                session.plan_markdown.rstrip(),
                "Current plan entries:",
                numbered_entries,
                index_guidance,
            )
        )

    def install_native_plan_tools(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        if self._owner._config.plan_provider is not None:
            return
        if has_native_plan_tools(agent):
            return

        def prepare_plan_access_tool(ctx: Any, tool_def: ToolDefinition) -> ToolDefinition | None:
            del ctx
            active_session = try_active_session(agent)
            if active_session is None:
                return None
            if not self.supports_native_plan_state(active_session):
                return None
            return tool_def

        def prepare_plan_progress_tool(ctx: Any, tool_def: ToolDefinition) -> ToolDefinition | None:
            prepared_tool = prepare_plan_access_tool(ctx, tool_def)
            if prepared_tool is None:
                return None
            active_session = try_active_session(agent)
            if active_session is None:
                return None
            if not self.supports_native_plan_progress(active_session):
                return None
            return prepared_tool

        tool_plain = agent.tool_plain

        @tool_plain(name=_GET_PLAN_TOOL_NAME, prepare=prepare_plan_access_tool)
        def acp_get_plan() -> str:
            """Return the saved plan and numbered entries.

            The returned entry numbers are 1-based. Use those same numbers with
            `acp_update_plan_entry` and `acp_mark_plan_done`.
            """
            active_session = try_active_session(agent)
            if active_session is None:
                return "No active ACP session is bound."
            return self.format_native_plan(active_session)

        @tool_plain(name=_SET_PLAN_TOOL_NAME, prepare=prepare_plan_access_tool)
        async def acp_set_plan(entries: list[PlanEntry], plan_md: str | None = None) -> str:
            """Replace the current plan state with the provided entries."""
            active_session = try_active_session(agent)
            if active_session is None:
                return "No active ACP session is bound."
            await self.persist_native_plan_state(
                active_session,
                agent=agent,
                entries=entries,
                plan_markdown=plan_md,
            )
            return f"Recorded {len(entries)} plan entries."

        @tool_plain(name=_UPDATE_PLAN_ENTRY_TOOL_NAME, prepare=prepare_plan_progress_tool)
        async def acp_update_plan_entry(
            index: int,
            status: str | None = None,
            content: str | None = None,
            priority: str | None = None,
        ) -> str:
            """Update a single plan entry by its 1-based index."""
            active_session = try_active_session(agent)
            if active_session is None:
                return "No active ACP session is bound."
            updated_entry = self.replace_native_plan_entry(
                active_session,
                index=index,
                status=status,
                content=content,
                priority=priority,
            )
            await self.persist_current_native_plan_state(active_session, agent=agent)
            return (
                f"Updated plan entry {index}: "
                f"[{updated_entry.status}] ({updated_entry.priority}) {updated_entry.content}"
            )

        @tool_plain(name=_MARK_PLAN_DONE_TOOL_NAME, prepare=prepare_plan_progress_tool)
        async def acp_mark_plan_done(index: int) -> str:
            """Mark a single plan entry completed by its 1-based index."""
            active_session = try_active_session(agent)
            if active_session is None:
                return "No active ACP session is bound."
            updated_entry = self.replace_native_plan_entry(
                active_session,
                index=index,
                status="completed",
            )
            await self.persist_current_native_plan_state(active_session, agent=agent)
            return f"Marked plan entry {index} as completed: {updated_entry.content}"

        set_native_plan_tools_installed(agent)
