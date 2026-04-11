from __future__ import annotations as _annotations

import asyncio
import traceback
from collections.abc import Sequence
from typing import TYPE_CHECKING, Generic, TypeAlias, TypeVar
from uuid import uuid4

from acp.schema import AgentMessageChunk, PromptResponse, TextContentBlock
from pydantic_ai import Agent as PydanticAgent

from ..session.state import AcpSessionContext, StoredSessionUpdate, utc_now
from ._prompt_runtime import NativePlanGeneration
from .prompts import (
    PromptBlock,
    PromptRunOutcome,
    build_cancelled_history,
    build_error_history,
    build_user_updates,
    derive_title,
    dump_message_history,
    prompt_to_text,
    sanitize_message_history,
    usage_from_run,
)
from .slash_commands import parse_slash_command

if TYPE_CHECKING:
    from .adapter import PydanticAcpAgent

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)
OutputDataT = TypeVar("OutputDataT", covariant=True)
PromptExecutionResult: TypeAlias = PromptRunOutcome | PromptResponse

__all__ = ("_AdapterPromptHandler",)


class _AdapterPromptHandler(Generic[AgentDepsT, OutputDataT]):
    def __init__(self, owner: PydanticAcpAgent[AgentDepsT, OutputDataT]) -> None:
        self._owner = owner

    async def prompt(
        self,
        prompt: list[PromptBlock],
        session_id: str,
        *,
        message_id: str | None,
    ) -> PromptResponse:
        session = self._owner._require_session(session_id)
        current_task = asyncio.current_task()
        if current_task is not None:
            self._owner._active_prompt_tasks[session_id] = current_task
        acknowledged_message_id = message_id or uuid4().hex
        prompt_text = self._prepare_prompt_session(
            prompt,
            session=session,
            acknowledged_message_id=acknowledged_message_id,
        )
        slash_command = parse_slash_command(prompt_text)

        agent = await self._owner._agent_source.get_agent(session)
        self._owner._configure_agent_runtime(session, agent)
        if slash_command is not None:
            slash_response = await self._owner._handle_slash_command(
                slash_command.name,
                argument=slash_command.argument,
                session=session,
                agent=agent,
            )
            if slash_response is not None:
                return await self._emit_slash_command_response(
                    session_id=session_id,
                    slash_response=slash_response,
                    acknowledged_message_id=acknowledged_message_id,
                )
        try:
            prompt_result = await self._run_prompt(
                agent=agent,
                prompt=prompt,
                session=session,
                prompt_text=prompt_text,
            )
            if isinstance(prompt_result, PromptResponse):
                return PromptResponse(
                    stop_reason=prompt_result.stop_reason,
                    usage=prompt_result.usage,
                    user_message_id=acknowledged_message_id,
                )
            return await self._finalize_prompt_outcome(
                session=session,
                agent=agent,
                prompt_outcome=prompt_result,
                acknowledged_message_id=acknowledged_message_id,
            )
        finally:
            active_task = self._owner._active_prompt_tasks.get(session_id)
            if active_task is current_task:
                self._owner._active_prompt_tasks.pop(session_id, None)

    def _prepare_prompt_session(
        self,
        prompt: Sequence[PromptBlock],
        *,
        session: AcpSessionContext,
        acknowledged_message_id: str,
    ) -> str:
        for update in build_user_updates(list(prompt), message_id=acknowledged_message_id):
            session.transcript.append(StoredSessionUpdate.from_update(update))

        prompt_text = prompt_to_text(list(prompt))
        if session.title is None and parse_slash_command(prompt_text) is None:
            session.title = derive_title(list(prompt))
        session.updated_at = utc_now()
        self._owner._config.session_store.save(session)
        return prompt_text

    async def _emit_slash_command_response(
        self,
        *,
        session_id: str,
        slash_response: str,
        acknowledged_message_id: str,
    ) -> PromptResponse:
        response_session = self._owner._require_session(session_id)
        await self._owner._record_update(
            response_session,
            AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(type="text", text=slash_response),
                message_id=uuid4().hex,
            ),
        )
        response_session.updated_at = utc_now()
        self._owner._config.session_store.save(response_session)
        return PromptResponse(
            stop_reason="end_turn",
            usage=None,
            user_message_id=acknowledged_message_id,
        )

    async def _run_prompt(
        self,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        prompt: list[PromptBlock],
        session: AcpSessionContext,
        prompt_text: str,
    ) -> PromptExecutionResult:
        try:
            return await self._owner._run_prompt(agent=agent, prompt=prompt, session=session)
        except asyncio.CancelledError:
            return await self._handle_cancelled_prompt(
                session=session,
                prompt_text=prompt_text,
            )
        except Exception as error:
            self._handle_prompt_error(session=session, prompt_text=prompt_text, error=error)
            raise

    async def _handle_cancelled_prompt(
        self,
        *,
        session: AcpSessionContext,
        prompt_text: str,
    ) -> PromptResponse:
        current_task = asyncio.current_task()
        if current_task is not None:
            current_task.uncancel()
        cancellation_details = "User requested cancellation."
        cancellation_message = "\n".join(
            (
                "User stopped the run.",
                "",
                "Run details:",
                cancellation_details,
            )
        )
        await self._owner._record_update(
            session,
            AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(type="text", text=cancellation_message),
                message_id=uuid4().hex,
            ),
        )
        session.message_history_json = build_cancelled_history(
            session.message_history_json,
            prompt_text=prompt_text,
            details_text=cancellation_details,
        )
        session.updated_at = utc_now()
        self._owner._config.session_store.save(session)
        return PromptResponse(
            stop_reason="cancelled",
            usage=None,
            user_message_id="",
        )

    def _handle_prompt_error(
        self,
        *,
        session: AcpSessionContext,
        prompt_text: str,
        error: BaseException,
    ) -> None:
        session.message_history_json = build_error_history(
            session.message_history_json,
            prompt_text=prompt_text,
            traceback_text="".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            ),
        )
        session.updated_at = utc_now()
        self._owner._config.session_store.save(session)

    async def _finalize_prompt_outcome(
        self,
        *,
        session: AcpSessionContext,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        prompt_outcome: PromptRunOutcome,
        acknowledged_message_id: str,
    ) -> PromptResponse:
        result = prompt_outcome.result

        output_text = ""
        if prompt_outcome.stop_reason != "cancelled":
            output_text = self._owner._synchronize_native_plan_output(
                session,
                result.output,
                streamed_output=prompt_outcome.streamed_output,
            )
            if isinstance(result.output, NativePlanGeneration):
                await self._owner._persist_current_native_plan_state(session, agent=agent)
            if output_text == "" and not prompt_outcome.streamed_output:
                output_text = self._owner._config.output_serializer.serialize(result.output)
        if output_text:
            await self._owner._record_update(
                session,
                AgentMessageChunk(
                    session_update="agent_message_chunk",
                    content=TextContentBlock(type="text", text=output_text),
                    message_id=uuid4().hex,
                ),
            )

        session.message_history_json = dump_message_history(
            sanitize_message_history(
                result.all_messages(),
                error_text=(
                    "Permission request cancelled."
                    if prompt_outcome.stop_reason == "cancelled"
                    else None
                ),
            )
        )
        session.updated_at = utc_now()
        self._owner._config.session_store.save(session)
        surface = await self._owner._build_session_surface(session, agent)
        await self._owner._emit_session_state_updates(
            session,
            surface,
            emit_available_commands=True,
            emit_config_options=True,
            emit_current_mode=True,
            emit_plan=not self._owner._consume_native_plan_update(session),
            emit_session_info=True,
        )
        return PromptResponse(
            stop_reason=prompt_outcome.stop_reason,
            usage=usage_from_run(result.usage()),
            user_message_id=acknowledged_message_id,
        )
