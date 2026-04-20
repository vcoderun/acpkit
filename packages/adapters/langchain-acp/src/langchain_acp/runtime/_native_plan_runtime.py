from __future__ import annotations as _annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

from acp.exceptions import RequestError
from acp.schema import (
    AgentPlanUpdate,
    PlanEntry,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
)

from ..plan import PlanGenerationType
from ..session.state import AcpSessionContext, utc_now

if TYPE_CHECKING:
    from ..providers import ConfigOption
    from .adapter import LangChainAcpAgent

_PLAN_GENERATION_CONFIG_OPTIONS: tuple[PlanGenerationType, ...] = (
    "tools",
    "structured",
)

__all__ = ("_NativePlanRuntime",)


class _NativePlanRuntime:
    def __init__(self, owner: LangChainAcpAgent) -> None:
        self._owner = owner

    def supports_native_plan_state(self, session: AcpSessionContext) -> bool:
        return self._owner._config.plan_provider is None and self.is_plan_mode(session)

    def supports_native_plan_progress(self, session: AcpSessionContext) -> bool:
        return self.is_plan_mode(session) and (
            self._owner._config.enable_plan_progress_tools
            or self.uses_tool_plan_generation(session)
        )

    def supports_native_plan_writes(self, session: AcpSessionContext) -> bool:
        return self.supports_native_plan_state(session) and self.uses_tool_plan_generation(session)

    def requires_structured_plan_output(self, session: AcpSessionContext) -> bool:
        return self.supports_native_plan_state(session) and self.uses_structured_plan_generation(
            session
        )

    def supports_plan_generation_selection(self) -> bool:
        return self._owner._config.plan_mode_id is not None

    def current_plan_generation_type(self, session: AcpSessionContext) -> PlanGenerationType:
        configured_value = session.config_values.get("plan_generation_type")
        if (
            isinstance(configured_value, str)
            and configured_value in _PLAN_GENERATION_CONFIG_OPTIONS
        ):
            return cast(PlanGenerationType, configured_value)
        return self._owner._config.default_plan_generation_type

    def uses_tool_plan_generation(self, session: AcpSessionContext) -> bool:
        return self.current_plan_generation_type(session) == "tools"

    def uses_structured_plan_generation(self, session: AcpSessionContext) -> bool:
        return self.current_plan_generation_type(session) == "structured"

    def is_plan_mode(self, session: AcpSessionContext) -> bool:
        plan_mode_id = self._owner._config.plan_mode_id
        return plan_mode_id is not None and session.session_mode_id == plan_mode_id

    async def config_options(self, session: AcpSessionContext) -> list[ConfigOption]:
        if not self.supports_plan_generation_selection():
            return []
        return [
            SessionConfigOptionSelect(
                id="plan_generation_type",
                name="Plan Generation",
                category="agent",
                description="How plan mode records ACP plan state.",
                type="select",
                current_value=self.current_plan_generation_type(session),
                options=[
                    SessionConfigSelectOption(
                        value=value,
                        name="Tool-Based" if value == "tools" else "Structured",
                    )
                    for value in _PLAN_GENERATION_CONFIG_OPTIONS
                ],
            )
        ]

    def get_native_plan_entries(self, session: AcpSessionContext) -> list[PlanEntry] | None:
        if self._owner._config.plan_provider is not None:
            return None
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

    async def emit_native_plan_update(self, session: AcpSessionContext) -> None:
        entries = self.get_native_plan_entries(session)
        if entries is None:
            return
        update = AgentPlanUpdate(session_update="plan", entries=entries)
        client = self._owner._client
        if client is None:
            return
        await self._owner._emit_update(client=client, session=session, update=update)

    async def persist_native_plan_state(
        self,
        session: AcpSessionContext,
        *,
        entries: Sequence[PlanEntry],
        plan_markdown: str | None,
    ) -> None:
        self.set_native_plan_state(session, entries=entries, plan_markdown=plan_markdown)
        persistence_provider = self._owner._config.native_plan_persistence_provider
        if persistence_provider is not None:
            await self._owner._await_maybe(
                persistence_provider.persist_plan_state(
                    session=session,
                    entries=entries,
                    plan_markdown=plan_markdown,
                )
            )
        session.updated_at = utc_now()
        self._owner._store.save(session)
        await self.emit_native_plan_update(session)

    async def persist_current_native_plan_state(self, session: AcpSessionContext) -> None:
        entries = self.get_native_plan_entries(session)
        if entries is None and session.plan_markdown is None:
            return
        await self.persist_native_plan_state(
            session,
            entries=() if entries is None else entries,
            plan_markdown=session.plan_markdown,
        )

    async def update_native_plan_entry(
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
        await self.persist_native_plan_state(
            session,
            entries=entries,
            plan_markdown=session.plan_markdown,
        )
        return updated_entry

    def format_native_plan(self, session: AcpSessionContext) -> str:
        entries = self.get_native_plan_entries(session)
        additional_instructions = self._native_plan_additional_instructions()
        if not entries:
            if session.plan_markdown:
                if additional_instructions is None:
                    return session.plan_markdown
                return "\n\n".join(
                    (
                        session.plan_markdown.rstrip(),
                        "Additional plan instructions:",
                        additional_instructions,
                    )
                )
            return "No plan has been recorded yet."
        numbered_entries = "\n".join(
            f"{index}. [{entry.status}] ({entry.priority}) {entry.content}"
            for index, entry in enumerate(entries, start=1)
        )
        index_guidance = (
            "Use these 1-based entry numbers with `acp_update_plan_entry` and `acp_mark_plan_done`."
        )
        sections = [index_guidance]
        if additional_instructions is not None:
            sections.extend(("Additional plan instructions:", additional_instructions))
        if not session.plan_markdown:
            sections.append(numbered_entries)
            return "\n\n".join(sections)
        return "\n\n".join(
            (
                session.plan_markdown.rstrip(),
                "Current plan entries:",
                numbered_entries,
                *sections,
            )
        )

    def _native_plan_additional_instructions(self) -> str | None:
        instructions = self._owner._config.native_plan_additional_instructions
        if instructions is None:
            return None
        normalized = instructions.strip()
        if normalized == "":
            return None
        return normalized
