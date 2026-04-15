from __future__ import annotations as _annotations

from contextlib import nullcontext
from typing import TYPE_CHECKING, Any, Generic, TypeVar
from uuid import uuid4

from acp.exceptions import RequestError
from acp.schema import AgentMessageChunk, TextContentBlock, ToolCallProgress, ToolCallStart
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai import AgentRunResult, AgentRunResultEvent
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UserError
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    RetryPromptPart,
    ToolCallPart,
)
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults

from ..approvals import ApprovalResolution
from ..projection import (
    _is_output_tool,
    build_tool_progress_update,
    build_tool_start_update,
    build_tool_updates,
    compose_projection_maps,
    extract_tool_call_locations,
)
from ..session.state import AcpSessionContext, utc_now
from .prompts import (
    PromptBlock,
    PromptInput,
    PromptRunOutcome,
    load_message_history,
    prompt_to_input,
)

if TYPE_CHECKING:
    from ._prompt_runtime import RunOutputType, _PromptRuntime

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)
OutputDataT = TypeVar("OutputDataT", covariant=True)

_MAX_DEFERRED_APPROVAL_ROUNDS = 8

__all__ = ("_PromptExecution",)


class _PromptExecution(Generic[AgentDepsT, OutputDataT]):
    def __init__(self, runtime: _PromptRuntime[AgentDepsT, OutputDataT]) -> None:
        self._runtime = runtime

    async def run_prompt(
        self,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        prompt: list[PromptBlock],
        session: AcpSessionContext,
    ) -> PromptRunOutcome:
        message_history = load_message_history(session.message_history_json)
        deferred_tool_results: DeferredToolResults | None = None
        prompt_input: PromptInput | None = prompt_to_input(prompt)

        for _ in range(_MAX_DEFERRED_APPROVAL_ROUNDS):
            run_kwargs, model_override, run_output_type = await self._prepare_run_inputs(
                agent=agent,
                prompt=prompt,
                session=session,
                message_history=message_history,
                deferred_tool_results=deferred_tool_results,
            )
            try:
                result, streamed_output = await self._runtime._execute_prompt(
                    agent=agent,
                    prompt_input=prompt_input,
                    run_kwargs=run_kwargs,
                    session=session,
                    model_override=model_override,
                    run_output_type=run_output_type,
                )
            except (ModelAPIError, ModelHTTPError, UserError):
                if not self._runtime._owner._restore_default_model(agent, session):
                    raise
                self._runtime._owner._config.session_store.save(session)
                run_kwargs, model_override, run_output_type = await self._prepare_run_inputs(
                    agent=agent,
                    prompt=prompt,
                    session=session,
                    message_history=message_history,
                    deferred_tool_results=deferred_tool_results,
                )
                result, streamed_output = await self._runtime._execute_prompt(
                    agent=agent,
                    prompt_input=prompt_input,
                    run_kwargs=run_kwargs,
                    session=session,
                    model_override=model_override,
                    run_output_type=run_output_type,
                )

            await self._record_execution_updates(
                session=session,
                agent=agent,
                result=result,
                model_override=model_override,
                run_output_type=run_output_type,
            )
            maybe_outcome = await self._resolve_deferred_outcome(
                session=session,
                result=result,
                streamed_output=streamed_output,
            )
            if maybe_outcome is not None:
                return maybe_outcome

            approval_resolution = await self._runtime._owner._resolve_deferred_approvals(
                session=session,
                requests=result.output,
            )
            if approval_resolution.cancelled:
                await self._runtime._owner._record_cancelled_approval(
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
            prompt_input = None

        raise RequestError.internal_error({"reason": "deferred_approval_loop_exceeded"})

    async def execute_prompt(
        self,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        prompt_input: PromptInput | None,
        run_kwargs: dict[str, Any],
        session: AcpSessionContext,
        model_override: Any,
        run_output_type: RunOutputType | None,
    ) -> tuple[AgentRunResult[Any], bool]:
        use_stream_events = self._runtime._owner._should_stream_text_responses(
            agent,
            model_override=model_override,
            output_type=run_output_type,
        )
        hook_context = (
            self._runtime._hook_context(agent=agent, session=session)
            if self._runtime._owner._config.hook_projection_map is not None
            else nullcontext()
        )
        with hook_context:
            if use_stream_events:
                return await self._runtime._owner._run_prompt_with_events(
                    agent=agent,
                    prompt_input=prompt_input,
                    run_kwargs=run_kwargs,
                    session=session,
                )
            result = await agent.run(prompt_input, **run_kwargs)
            return result, False

    async def record_tool_updates(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        messages: list[ModelMessage],
    ) -> None:
        if not self._runtime._owner._config.enable_generic_tool_projection:
            return
        for update in build_tool_updates(
            messages,
            classifier=self._runtime._owner._tool_classifier,
            cwd=session.cwd,
            known_starts=self.known_tool_call_starts(session),
            projection_map=compose_projection_maps(self._runtime._owner._config.projection_maps),
            serializer=self._runtime._owner._config.output_serializer,
        ):
            await self._runtime._record_update(session, update)
        await self.record_bridge_updates(session, agent)

    async def record_bridge_updates(
        self,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
    ) -> None:
        for update in self._runtime._owner._bridge_manager.drain_updates(session, agent):
            await self._runtime._record_update(session, update)

    async def run_prompt_with_events(
        self,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        prompt_input: PromptInput | None,
        run_kwargs: dict[str, Any],
        session: AcpSessionContext,
    ) -> tuple[AgentRunResult[Any], bool]:
        known_starts = self.known_tool_call_starts(session)
        message_id = uuid4().hex
        projection_map = compose_projection_maps(self._runtime._owner._config.projection_maps)
        streamed_output = False

        async for event in agent.run_stream_events(prompt_input, **run_kwargs):
            if isinstance(event, AgentRunResultEvent):
                return event.result, streamed_output
            if self._runtime._owner._config.enable_generic_tool_projection and isinstance(
                event,
                FunctionToolCallEvent,
            ):
                if _is_output_tool(event.part.tool_name) or event.part.tool_call_id in known_starts:
                    continue
                start_update = build_tool_start_update(
                    event.part,
                    classifier=self._runtime._owner._tool_classifier,
                    cwd=session.cwd,
                    projection_map=projection_map,
                )
                known_starts[event.part.tool_call_id] = start_update
                await self._runtime._record_update(session, start_update)
                continue
            if self._runtime._owner._config.enable_generic_tool_projection and isinstance(
                event,
                FunctionToolResultEvent,
            ):
                result_part = event.result
                if isinstance(result_part, RetryPromptPart):
                    if result_part.tool_name is None or _is_output_tool(result_part.tool_name):
                        continue
                elif _is_output_tool(result_part.tool_name):
                    continue
                await self._runtime._record_update(
                    session,
                    build_tool_progress_update(
                        result_part,
                        classifier=self._runtime._owner._tool_classifier,
                        cwd=session.cwd,
                        known_start=known_starts.get(result_part.tool_call_id),
                        projection_map=projection_map,
                        serializer=self._runtime._owner._config.output_serializer,
                    ),
                )
                continue
            text_chunk = self._runtime._text_chunk_from_event(event)
            if text_chunk is None or text_chunk == "":
                continue
            streamed_output = True
            await self._runtime._record_update(
                session,
                AgentMessageChunk(
                    session_update="agent_message_chunk",
                    content=TextContentBlock(type="text", text=text_chunk),
                    message_id=message_id,
                ),
            )

        raise RequestError.internal_error({"reason": "missing_agent_run_result"})

    def known_tool_call_starts(self, session: AcpSessionContext) -> dict[str, ToolCallStart]:
        known_starts: dict[str, ToolCallStart] = {}
        for stored_update in session.transcript:
            update = stored_update.to_update()
            if not isinstance(update, ToolCallStart):
                continue
            known_starts[update.tool_call_id] = update
        return known_starts

    async def resolve_deferred_approvals(
        self,
        *,
        session: AcpSessionContext,
        requests: DeferredToolRequests,
    ) -> ApprovalResolution:
        approval_bridge = self._runtime._owner._config.approval_bridge
        if approval_bridge is None or self._runtime._owner._client is None:
            raise RequestError.internal_error({"reason": "deferred_approval_requires_client"})
        return await approval_bridge.resolve_deferred_approvals(
            client=self._runtime._owner._client,
            session=session,
            requests=requests,
            classifier=self._runtime._owner._tool_classifier,
        )

    async def record_cancelled_approval(
        self,
        session: AcpSessionContext,
        tool_call: ToolCallPart | None,
    ) -> None:
        if tool_call is None:
            return
        raw_input = tool_call.args_as_dict()
        await self._runtime._record_update(
            session,
            ToolCallProgress(
                session_update="tool_call_update",
                tool_call_id=tool_call.tool_call_id,
                title=tool_call.tool_name,
                kind=self._runtime._owner._tool_classifier.classify(tool_call.tool_name, raw_input),
                locations=extract_tool_call_locations(raw_input),
                status="failed",
                raw_output="Permission request cancelled.",
            ),
        )

    async def _prepare_run_inputs(
        self,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        prompt: list[PromptBlock],
        session: AcpSessionContext,
        message_history: list[ModelMessage] | None,
        deferred_tool_results: DeferredToolResults | None,
    ) -> tuple[dict[str, Any], Any, RunOutputType | None]:
        deps = await self._runtime._owner._agent_source.get_deps(session, agent)
        model_override = await self._runtime._owner._resolve_model_override(session, agent)
        model_override = await self._runtime._owner._resolve_prompt_model_override(
            session,
            agent,
            prompt=prompt,
            model_override=model_override,
        )
        run_output_type = self._runtime._owner._build_run_output_type(agent, session=session)
        run_kwargs = self._runtime._owner._build_run_kwargs(
            message_history=message_history,
            deferred_tool_results=deferred_tool_results,
            deps=deps,
            model_override=model_override,
            model_settings=self._runtime._owner._bridge_manager.get_model_settings(session, agent),
            output_type=run_output_type,
        )
        return run_kwargs, model_override, run_output_type

    async def _record_execution_updates(
        self,
        *,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        result: AgentRunResult[Any],
        model_override: Any,
        run_output_type: RunOutputType | None,
    ) -> None:
        if self._runtime._owner._should_stream_text_responses(
            agent,
            model_override=model_override,
            output_type=run_output_type,
        ):
            await self.record_bridge_updates(session, agent)
            return
        await self.record_tool_updates(session, agent, result.new_messages())

    async def _resolve_deferred_outcome(
        self,
        *,
        session: AcpSessionContext,
        result: AgentRunResult[Any],
        streamed_output: bool,
    ) -> PromptRunOutcome | None:
        if not isinstance(result.output, DeferredToolRequests):
            return PromptRunOutcome(
                result=result,
                stop_reason="end_turn",
                streamed_output=streamed_output,
            )
        if not self._runtime._owner._supports_deferred_approval_bridge():
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
        self._runtime._owner._config.session_store.save(session)
        return None
