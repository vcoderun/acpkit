from __future__ import annotations as _annotations

from collections.abc import Sequence
from contextlib import nullcontext
from typing import TYPE_CHECKING, Any, Generic, TypeAlias, TypeVar
from uuid import uuid4

from acp.exceptions import RequestError
from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    PlanEntry,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
)
from pydantic import BaseModel
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai import AgentRunResult, AgentRunResultEvent
from pydantic_ai import models as pydantic_models
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UserError
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ToolCallPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.output import OutputSpec
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolDefinition

from ..approvals import ApprovalResolution
from ..awaitables import resolve_value
from ..bridges import PrepareToolsBridge
from ..models import ModelOverride
from ..projection import (
    _is_output_tool,
    build_tool_progress_update,
    build_tool_start_update,
    build_tool_updates,
    compose_projection_maps,
    extract_tool_call_locations,
)
from ..session.state import (
    AcpSessionContext,
    SessionTranscriptUpdate,
    StoredSessionUpdate,
    utc_now,
)
from ._agent_state import (
    has_native_plan_tools,
    set_native_plan_tools_installed,
    try_active_session,
)
from .hook_introspection import observe_agent_hooks
from .prompts import PromptBlock, PromptRunOutcome, load_message_history, prompt_to_text

if TYPE_CHECKING:
    from .adapter import PydanticAcpAgent

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)
OutputDataT = TypeVar("OutputDataT", covariant=True)
RunOutputType: TypeAlias = OutputSpec[Any]

_MAX_DEFERRED_APPROVAL_ROUNDS = 8
_GET_PLAN_TOOL_NAME = "acp_get_plan"
_SET_PLAN_TOOL_NAME = "acp_set_plan"
_UPDATE_PLAN_ENTRY_TOOL_NAME = "acp_update_plan_entry"
_MARK_PLAN_DONE_TOOL_NAME = "acp_mark_plan_done"

__all__ = ("NativePlanGeneration", "_PromptRuntime")


class NativePlanGeneration(BaseModel):
    plan_md: str
    plan_entries: list[PlanEntry]


class _PromptRuntime(Generic[AgentDepsT, OutputDataT]):
    def __init__(self, owner: PydanticAcpAgent[AgentDepsT, OutputDataT]) -> None:
        self._owner = owner
        self._native_plan_updates: set[str] = set()

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
        message_history = load_message_history(session.message_history_json)
        deferred_tool_results: DeferredToolResults | None = None
        prompt_text: str | None = prompt_to_text(prompt)

        for _ in range(_MAX_DEFERRED_APPROVAL_ROUNDS):
            deps = await self._owner._agent_source.get_deps(session, agent)
            model_override = await self._owner._resolve_model_override(session, agent)
            run_output_type = self._owner._build_run_output_type(agent, session=session)
            run_kwargs = self._owner._build_run_kwargs(
                message_history=message_history,
                deferred_tool_results=deferred_tool_results,
                deps=deps,
                model_override=model_override,
                model_settings=self._owner._bridge_manager.get_model_settings(session, agent),
                output_type=run_output_type,
            )
            try:
                result, streamed_output = await self._execute_prompt(
                    agent=agent,
                    prompt_text=prompt_text,
                    run_kwargs=run_kwargs,
                    session=session,
                    model_override=model_override,
                    run_output_type=run_output_type,
                )
            except (ModelAPIError, ModelHTTPError, UserError):
                if not self._owner._restore_default_model(agent, session):
                    raise
                self._owner._config.session_store.save(session)
                deps = await self._owner._agent_source.get_deps(session, agent)
                model_override = await self._owner._resolve_model_override(session, agent)
                run_kwargs = self._owner._build_run_kwargs(
                    message_history=message_history,
                    deferred_tool_results=deferred_tool_results,
                    deps=deps,
                    model_override=model_override,
                    model_settings=self._owner._bridge_manager.get_model_settings(session, agent),
                    output_type=run_output_type,
                )
                result, streamed_output = await self._execute_prompt(
                    agent=agent,
                    prompt_text=prompt_text,
                    run_kwargs=run_kwargs,
                    session=session,
                    model_override=model_override,
                    run_output_type=run_output_type,
                )

            if self._owner._should_stream_text_responses(
                agent,
                model_override=model_override,
                output_type=run_output_type,
            ):
                await self._owner._record_bridge_updates(session, agent)
            else:
                await self._owner._record_tool_updates(session, agent, result.new_messages())

            if not isinstance(result.output, DeferredToolRequests):
                return PromptRunOutcome(
                    result=result,
                    stop_reason="end_turn",
                    streamed_output=streamed_output,
                )
            if not self._owner._supports_deferred_approval_bridge():
                return PromptRunOutcome(
                    result=result,
                    stop_reason="end_turn",
                    streamed_output=streamed_output,
                )
            if result.output.calls or not result.output.approvals:
                return PromptRunOutcome(
                    result=result,
                    stop_reason="end_turn",
                    streamed_output=streamed_output,
                )

            session.message_history_json = result.all_messages_json().decode("utf-8")
            session.updated_at = utc_now()
            self._owner._config.session_store.save(session)

            approval_resolution = await self._owner._resolve_deferred_approvals(
                session=session,
                requests=result.output,
            )
            if approval_resolution.cancelled:
                await self._owner._record_cancelled_approval(
                    session,
                    approval_resolution.cancelled_tool_call,
                )
                return PromptRunOutcome(
                    result=result,
                    stop_reason="cancelled",
                    streamed_output=streamed_output,
                )

            message_history = result.all_messages()
            deferred_tool_results = approval_resolution.deferred_tool_results
            prompt_text = None

        raise RequestError.internal_error({"reason": "deferred_approval_loop_exceeded"})

    async def _execute_prompt(
        self,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        prompt_text: str | None,
        run_kwargs: dict[str, Any],
        session: AcpSessionContext,
        model_override: ModelOverride | None,
        run_output_type: RunOutputType | None,
    ) -> tuple[AgentRunResult[Any], bool]:
        use_stream_events = self._owner._should_stream_text_responses(
            agent,
            model_override=model_override,
            output_type=run_output_type,
        )
        hook_context = (
            observe_agent_hooks(
                agent,
                write_update=lambda update: self._owner._record_update(session, update),
                projection_map=self._owner._config.hook_projection_map,
            )
            if self._owner._config.hook_projection_map is not None
            else nullcontext()
        )
        with hook_context:
            if use_stream_events:
                return await self._owner._run_prompt_with_events(
                    agent=agent,
                    prompt_text=prompt_text,
                    run_kwargs=run_kwargs,
                    session=session,
                )
            result = await agent.run(prompt_text, **run_kwargs)
            return result, False

    async def _record_tool_updates(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        messages: list[ModelMessage],
    ) -> None:
        if not self._owner._config.enable_generic_tool_projection:
            return
        for update in build_tool_updates(
            messages,
            classifier=self._owner._tool_classifier,
            cwd=session.cwd,
            known_starts=self._known_tool_call_starts(session),
            projection_map=compose_projection_maps(self._owner._config.projection_maps),
            serializer=self._owner._config.output_serializer,
        ):
            await self._record_update(session, update)
        await self._record_bridge_updates(session, agent)

    async def _record_bridge_updates(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        for update in self._owner._bridge_manager.drain_updates(session, agent):
            await self._record_update(session, update)

    async def _run_prompt_with_events(
        self,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        prompt_text: str | None,
        run_kwargs: dict[str, Any],
        session: AcpSessionContext,
    ) -> tuple[AgentRunResult[Any], bool]:
        known_starts = self._known_tool_call_starts(session)
        message_id = uuid4().hex
        projection_map = compose_projection_maps(self._owner._config.projection_maps)
        streamed_output = False

        async for event in agent.run_stream_events(prompt_text, **run_kwargs):
            if isinstance(event, AgentRunResultEvent):
                return event.result, streamed_output
            if self._owner._config.enable_generic_tool_projection and isinstance(
                event,
                FunctionToolCallEvent,
            ):
                if _is_output_tool(event.part.tool_name) or event.part.tool_call_id in known_starts:
                    continue
                start_update = build_tool_start_update(
                    event.part,
                    classifier=self._owner._tool_classifier,
                    cwd=session.cwd,
                    projection_map=projection_map,
                )
                known_starts[event.part.tool_call_id] = start_update
                await self._record_update(session, start_update)
                continue
            if self._owner._config.enable_generic_tool_projection and isinstance(
                event,
                FunctionToolResultEvent,
            ):
                result_part = event.result
                if isinstance(result_part, RetryPromptPart):
                    if result_part.tool_name is None or _is_output_tool(result_part.tool_name):
                        continue
                elif _is_output_tool(result_part.tool_name):
                    continue
                await self._record_update(
                    session,
                    build_tool_progress_update(
                        result_part,
                        classifier=self._owner._tool_classifier,
                        cwd=session.cwd,
                        known_start=known_starts.get(result_part.tool_call_id),
                        projection_map=projection_map,
                        serializer=self._owner._config.output_serializer,
                    ),
                )
                continue
            text_chunk = self._text_chunk_from_event(event)
            if text_chunk is None or text_chunk == "":
                continue
            streamed_output = True
            await self._record_update(
                session,
                AgentMessageChunk(
                    session_update="agent_message_chunk",
                    content=TextContentBlock(type="text", text=text_chunk),
                    message_id=message_id,
                ),
            )

        raise RequestError.internal_error({"reason": "missing_agent_run_result"})

    def _known_tool_call_starts(self, session: AcpSessionContext) -> dict[str, ToolCallStart]:
        known_starts: dict[str, ToolCallStart] = {}
        for stored_update in session.transcript:
            update = stored_update.to_update()
            if not isinstance(update, ToolCallStart):
                continue
            known_starts[update.tool_call_id] = update
        return known_starts

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
        approval_bridge = self._owner._config.approval_bridge
        if approval_bridge is None or self._owner._client is None:
            raise RequestError.internal_error({"reason": "deferred_approval_requires_client"})
        return await approval_bridge.resolve_deferred_approvals(
            client=self._owner._client,
            session=session,
            requests=requests,
            classifier=self._owner._tool_classifier,
        )

    async def _record_cancelled_approval(
        self,
        session: AcpSessionContext,
        tool_call: ToolCallPart | None,
    ) -> None:
        if tool_call is None:
            return
        raw_input = tool_call.args_as_dict()
        await self._record_update(
            session,
            ToolCallProgress(
                session_update="tool_call_update",
                tool_call_id=tool_call.tool_call_id,
                title=tool_call.tool_name,
                kind=self._owner._tool_classifier.classify(tool_call.tool_name, raw_input),
                locations=extract_tool_call_locations(raw_input),
                status="failed",
                raw_output="Permission request cancelled.",
            ),
        )

    def _build_run_output_type(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        session: AcpSessionContext,
    ) -> RunOutputType | None:
        output_type: RunOutputType = agent.output_type
        if self._requires_native_plan_output(session):
            output_type = NativePlanGeneration
        if not self._supports_deferred_approval_bridge():
            return output_type if output_type is not agent.output_type else None
        if output_type is DeferredToolRequests:
            return output_type
        if (
            isinstance(output_type, Sequence)
            and not isinstance(output_type, str)
            and DeferredToolRequests in output_type
        ):
            return output_type
        return [output_type, DeferredToolRequests]

    def _should_stream_text_responses(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        model_override: ModelOverride | None,
        output_type: RunOutputType | None,
    ) -> bool:
        candidate_output_type = agent.output_type if output_type is None else output_type
        return self._contains_text_output(candidate_output_type) and self._supports_streaming_model(
            agent,
            model_override=model_override,
        )

    def _contains_text_output(self, output_type: Any) -> bool:
        if output_type is str or output_type is NativePlanGeneration:
            return True
        if isinstance(output_type, Sequence) and not isinstance(output_type, str):
            return any(self._contains_text_output(item) for item in output_type)
        return False

    def _contains_native_plan_generation(self, output_type: Any) -> bool:
        if output_type is NativePlanGeneration:
            return True
        if isinstance(output_type, Sequence) and not isinstance(output_type, str):
            return any(self._contains_native_plan_generation(item) for item in output_type)
        return False

    def _supports_streaming_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        model_override: ModelOverride | None,
    ) -> bool:
        model = self._resolve_runtime_model(agent, model_override=model_override)
        if isinstance(model, FunctionModel):
            return model.stream_function is not None
        return type(model).request_stream is not pydantic_models.Model.request_stream

    def _resolve_runtime_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        model_override: ModelOverride | None,
    ) -> pydantic_models.Model:
        if model_override is None:
            model = agent.model
            if isinstance(model, pydantic_models.Model):
                return model
            if isinstance(model, str):
                try:
                    return pydantic_models.infer_model(model)
                except ValueError as exc:
                    raise UserError(str(exc)) from exc
            raise RequestError.internal_error({"reason": "agent_model_missing"})
        if isinstance(model_override, pydantic_models.Model):
            return model_override
        try:
            return pydantic_models.infer_model(model_override)
        except ValueError as exc:
            raise UserError(str(exc)) from exc

    def _text_chunk_from_event(self, event: Any) -> str | None:
        if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
            return event.part.content
        if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
            return event.delta.content_delta
        return None

    def _synchronize_native_plan_output(
        self,
        session: AcpSessionContext,
        output: Any,
        *,
        streamed_output: bool,
    ) -> str:
        if not isinstance(output, NativePlanGeneration):
            return ""
        self._set_native_plan_state(
            session,
            entries=output.plan_entries,
            plan_markdown=output.plan_md,
        )
        if streamed_output:
            return ""
        return output.plan_md

    def _native_plan_bridge(
        self,
        session: AcpSessionContext,
    ) -> PrepareToolsBridge[Any] | None:
        for bridge in self._owner._config.capability_bridges:
            if isinstance(bridge, PrepareToolsBridge) and bridge.supports_plan_tools(session):
                return bridge
        return None

    def _supports_native_plan_state(self, session: AcpSessionContext) -> bool:
        return (
            self._owner._config.plan_provider is None
            and self._native_plan_bridge(session) is not None
        )

    def _requires_native_plan_output(self, session: AcpSessionContext) -> bool:
        bridge = self._native_plan_bridge(session)
        if bridge is None:
            return False
        return bridge.is_plan_mode(session)

    def _supports_native_plan_progress(self, session: AcpSessionContext) -> bool:
        bridge = self._native_plan_bridge(session)
        if bridge is None:
            return False
        return bridge.current_mode(session).plan_tools

    def _get_native_plan_entries(self, session: AcpSessionContext) -> list[PlanEntry] | None:
        if not session.plan_entries:
            return None
        return [PlanEntry.model_validate(entry) for entry in session.plan_entries]

    def _set_native_plan_state(
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

    async def _persist_external_plan_state(
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

    async def _emit_native_plan_update(self, session: AcpSessionContext) -> None:
        client = self._owner._client
        if client is None:
            return
        entries = self._get_native_plan_entries(session)
        if entries is None:
            return
        self._native_plan_updates.add(session.session_id)
        await client.session_update(
            session_id=session.session_id,
            update=AgentPlanUpdate(session_update="plan", entries=entries),
        )

    def _consume_native_plan_update(self, session: AcpSessionContext) -> bool:
        if session.session_id not in self._native_plan_updates:
            return False
        self._native_plan_updates.remove(session.session_id)
        return True

    async def _persist_native_plan_state(
        self,
        session: AcpSessionContext,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        entries: Sequence[PlanEntry],
        plan_markdown: str | None,
    ) -> None:
        self._set_native_plan_state(
            session,
            entries=entries,
            plan_markdown=plan_markdown,
        )
        await self._persist_external_plan_state(
            session,
            agent=agent,
            entries=entries,
            plan_markdown=plan_markdown,
        )
        session.updated_at = utc_now()
        self._owner._config.session_store.save(session)
        await self._emit_native_plan_update(session)

    async def _persist_current_native_plan_state(
        self,
        session: AcpSessionContext,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        entries = self._get_native_plan_entries(session)
        if entries is None and session.plan_markdown is None:
            return
        await self._persist_native_plan_state(
            session,
            agent=agent,
            entries=() if entries is None else entries,
            plan_markdown=session.plan_markdown,
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
        entries = self._get_native_plan_entries(session)
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
        self._set_native_plan_state(
            session,
            entries=entries,
            plan_markdown=session.plan_markdown,
        )
        return updated_entry

    def _format_native_plan(self, session: AcpSessionContext) -> str:
        entries = self._get_native_plan_entries(session)
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

    def _install_native_plan_tools(
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
            if not self._supports_native_plan_state(active_session):
                return None
            return tool_def

        def prepare_plan_progress_tool(ctx: Any, tool_def: ToolDefinition) -> ToolDefinition | None:
            prepared_tool = prepare_plan_access_tool(ctx, tool_def)
            if prepared_tool is None:
                return None
            active_session = try_active_session(agent)
            if active_session is None:
                return None
            if not self._supports_native_plan_progress(active_session):
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
            return self._format_native_plan(active_session)

        @tool_plain(name=_SET_PLAN_TOOL_NAME, prepare=prepare_plan_access_tool)
        async def acp_set_plan(entries: list[PlanEntry], plan_md: str | None = None) -> str:
            """Replace the current plan state with the provided entries."""
            active_session = try_active_session(agent)
            if active_session is None:
                return "No active ACP session is bound."
            await self._persist_native_plan_state(
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
            updated_entry = self._replace_native_plan_entry(
                active_session,
                index=index,
                status=status,
                content=content,
                priority=priority,
            )
            await self._persist_current_native_plan_state(active_session, agent=agent)
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
            updated_entry = self._replace_native_plan_entry(
                active_session,
                index=index,
                status="completed",
            )
            await self._persist_current_native_plan_state(active_session, agent=agent)
            return f"Marked plan entry {index} as completed: {updated_entry.content}"

        set_native_plan_tools_installed(agent)
