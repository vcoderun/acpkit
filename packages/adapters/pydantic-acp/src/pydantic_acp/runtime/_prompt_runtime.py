from __future__ import annotations as _annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any, Generic, TypeAlias, TypeVar

from acp.schema import PlanEntry, ToolCallStart
from pydantic import BaseModel
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai import AgentRunResult
from pydantic_ai import models as pydantic_models
from pydantic_ai.messages import (
    ModelMessage,
    ToolCallPart,
)
from pydantic_ai.output import OutputSpec
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults

from ..approvals import ApprovalResolution
from ..bridges import PrepareToolsBridge
from ..models import ModelOverride
from ..session.state import AcpSessionContext, SessionTranscriptUpdate, StoredSessionUpdate
from ._native_plan_runtime import _NativePlanRuntime
from ._prompt_execution import _PromptExecution
from ._prompt_model_runtime import _PromptModelRuntime
from .prompts import PromptBlock, PromptInput, PromptRunOutcome

if TYPE_CHECKING:
    from .adapter import PydanticAcpAgent

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)
OutputDataT = TypeVar("OutputDataT", covariant=True)
RunOutputType: TypeAlias = OutputSpec[Any]

__all__ = ("TaskPlan", "NativePlanGeneration", "_PromptRuntime")


class TaskPlan(BaseModel):
    plan_md: str
    plan_entries: list[PlanEntry]


NativePlanGeneration = TaskPlan


class _PromptRuntime(Generic[AgentDepsT, OutputDataT]):
    def __init__(self, owner: PydanticAcpAgent[AgentDepsT, OutputDataT]) -> None:
        self._owner = owner
        self._native_plan_runtime = _NativePlanRuntime(owner)
        self._execution = _PromptExecution(self)
        self._model_runtime = _PromptModelRuntime(self, native_plan_type=TaskPlan)

    async def _record_update(
        self,
        session: AcpSessionContext,
        update: SessionTranscriptUpdate,
    ) -> None:
        session.transcript.append(StoredSessionUpdate.from_update(update))
        if self._owner._client is not None:
            await self._owner._client.session_update(session_id=session.session_id, update=update)

    async def _replay_transcript(self, session: AcpSessionContext) -> None:
        if not self._owner._config.replay_history_on_load or self._owner._client is None:
            return
        for stored_update in session.transcript:
            await self._owner._client.session_update(
                session_id=session.session_id,
                update=stored_update.to_update(),
            )

    async def _run_prompt(
        self,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        prompt: list[PromptBlock],
        session: AcpSessionContext,
    ) -> PromptRunOutcome:
        return await self._execution.run_prompt(agent=agent, prompt=prompt, session=session)

    async def _execute_prompt(
        self,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        prompt_input: PromptInput | None,
        run_kwargs: dict[str, Any],
        session: AcpSessionContext,
        model_override: ModelOverride | None,
        run_output_type: RunOutputType | None,
    ) -> tuple[AgentRunResult[Any], bool]:
        return await self._execution.execute_prompt(
            agent=agent,
            prompt_input=prompt_input,
            run_kwargs=run_kwargs,
            session=session,
            model_override=model_override,
            run_output_type=run_output_type,
        )

    async def _record_tool_updates(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        messages: list[ModelMessage],
    ) -> None:
        await self._execution.record_tool_updates(session, agent, messages)

    async def _record_bridge_updates(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        await self._execution.record_bridge_updates(session, agent)

    async def _run_prompt_with_events(
        self,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        prompt_input: PromptInput | None,
        run_kwargs: dict[str, Any],
        session: AcpSessionContext,
    ) -> tuple[AgentRunResult[Any], bool]:
        return await self._execution.run_prompt_with_events(
            agent=agent,
            prompt_input=prompt_input,
            run_kwargs=run_kwargs,
            session=session,
        )

    def _known_tool_call_starts(self, session: AcpSessionContext) -> dict[str, ToolCallStart]:
        return self._execution.known_tool_call_starts(session)

    def _build_run_kwargs(
        self,
        *,
        message_history: list[ModelMessage] | None,
        deferred_tool_results: DeferredToolResults | None,
        deps: AgentDepsT | None,
        model_override: ModelOverride | None,
        model_settings: ModelSettings | None,
        output_type: RunOutputType | None,
    ) -> dict[str, Any]:
        run_kwargs: dict[str, Any] = {
            "message_history": message_history,
            "deferred_tool_results": deferred_tool_results,
            "model": model_override,
        }
        if deps is not None:
            run_kwargs["deps"] = deps
        if model_settings is not None:
            run_kwargs["model_settings"] = model_settings
        if output_type is not None:
            run_kwargs["output_type"] = output_type
        return run_kwargs

    def _supports_deferred_approval_bridge(self) -> bool:
        return self._owner._config.approval_bridge is not None

    async def _resolve_deferred_approvals(
        self,
        *,
        session: AcpSessionContext,
        requests: DeferredToolRequests,
    ) -> ApprovalResolution:
        return await self._execution.resolve_deferred_approvals(
            session=session,
            requests=requests,
        )

    async def _record_cancelled_approval(
        self,
        session: AcpSessionContext,
        tool_call: ToolCallPart | None,
    ) -> None:
        await self._execution.record_cancelled_approval(session, tool_call)

    def _build_run_output_type(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        session: AcpSessionContext,
    ) -> RunOutputType | None:
        return self._model_runtime.build_run_output_type(agent, session=session)

    def _should_stream_text_responses(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        model_override: ModelOverride | None,
        output_type: RunOutputType | None,
    ) -> bool:
        return self._model_runtime.should_stream_text_responses(
            agent,
            model_override=model_override,
            output_type=output_type,
        )

    def _contains_text_output(self, output_type: Any) -> bool:
        return self._model_runtime.contains_text_output(output_type)

    def _contains_native_plan_generation(self, output_type: Any) -> bool:
        return self._model_runtime.contains_native_plan_generation(output_type)

    def _supports_streaming_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        model_override: ModelOverride | None,
    ) -> bool:
        return self._model_runtime.supports_streaming_model(
            agent,
            model_override=model_override,
        )

    def _resolve_runtime_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        model_override: ModelOverride | None,
    ) -> pydantic_models.Model:
        return self._model_runtime.resolve_runtime_model(
            agent,
            model_override=model_override,
        )

    def _text_chunk_from_event(self, event: Any) -> str | None:
        return self._model_runtime.text_chunk_from_event(event)

    def _hook_context(
        self,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        session: AcpSessionContext,
    ) -> AbstractContextManager[None]:
        return self._model_runtime.hook_context(agent=agent, session=session)

    def _synchronize_native_plan_output(
        self,
        session: AcpSessionContext,
        output: Any,
        *,
        streamed_output: bool,
    ) -> str:
        return self._model_runtime.synchronize_native_plan_output(
            session,
            output,
            streamed_output=streamed_output,
        )

    def _native_plan_bridge(
        self,
        session: AcpSessionContext,
    ) -> PrepareToolsBridge[Any] | None:
        return self._native_plan_runtime.native_plan_bridge(session)

    def _supports_native_plan_state(self, session: AcpSessionContext) -> bool:
        return self._native_plan_runtime.supports_native_plan_state(session)

    def _requires_native_plan_output(self, session: AcpSessionContext) -> bool:
        return self._native_plan_runtime.requires_native_plan_output(session)

    def _supports_native_plan_progress(self, session: AcpSessionContext) -> bool:
        return self._native_plan_runtime.supports_native_plan_progress(session)

    def _get_native_plan_entries(self, session: AcpSessionContext) -> list[PlanEntry] | None:
        return self._native_plan_runtime.get_native_plan_entries(session)

    def _set_native_plan_state(
        self,
        session: AcpSessionContext,
        *,
        entries: Sequence[PlanEntry],
        plan_markdown: str | None,
    ) -> None:
        self._native_plan_runtime.set_native_plan_state(
            session,
            entries=entries,
            plan_markdown=plan_markdown,
        )

    async def _persist_external_plan_state(
        self,
        session: AcpSessionContext,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        entries: Sequence[PlanEntry],
        plan_markdown: str | None,
    ) -> None:
        await self._native_plan_runtime.persist_external_plan_state(
            session,
            agent=agent,
            entries=entries,
            plan_markdown=plan_markdown,
        )

    async def _emit_native_plan_update(self, session: AcpSessionContext) -> None:
        await self._native_plan_runtime.emit_native_plan_update(session)

    def _consume_native_plan_update(self, session: AcpSessionContext) -> bool:
        return self._native_plan_runtime.consume_native_plan_update(session)

    async def _persist_native_plan_state(
        self,
        session: AcpSessionContext,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        entries: Sequence[PlanEntry],
        plan_markdown: str | None,
    ) -> None:
        await self._native_plan_runtime.persist_native_plan_state(
            session,
            agent=agent,
            entries=entries,
            plan_markdown=plan_markdown,
        )

    async def _persist_current_native_plan_state(
        self,
        session: AcpSessionContext,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        await self._native_plan_runtime.persist_current_native_plan_state(
            session,
            agent=agent,
        )

    def _replace_native_plan_entry(
        self,
        session: AcpSessionContext,
        *,
        index: int,
        status: str | None = None,
        content: str | None = None,
        priority: str | None = None,
    ) -> PlanEntry:
        return self._native_plan_runtime.replace_native_plan_entry(
            session,
            index=index,
            status=status,
            content=content,
            priority=priority,
        )

    def _format_native_plan(self, session: AcpSessionContext) -> str:
        return self._native_plan_runtime.format_native_plan(session)

    def _install_native_plan_tools(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        self._native_plan_runtime.install_native_plan_tools(agent)
