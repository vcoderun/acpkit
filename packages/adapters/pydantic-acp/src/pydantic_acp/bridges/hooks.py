from __future__ import annotations as _annotations

from collections.abc import AsyncIterable
from dataclasses import dataclass
from typing import Any

from pydantic_ai import AgentRunResult
from pydantic_ai.capabilities import (
    AgentNode,
    Hooks,
    NodeResult,
    RawToolArgs,
    ValidatedToolArgs,
    WrapModelRequestHandler,
    WrapNodeRunHandler,
    WrapRunHandler,
    WrapToolExecuteHandler,
    WrapToolValidateHandler,
)
from pydantic_ai.messages import AgentStreamEvent, ModelResponse, ToolCallPart
from pydantic_ai.tools import RunContext, ToolDefinition

from ..agent_types import RuntimeAgent
from ..session.state import AcpSessionContext, JsonValue
from .base import BufferedCapabilityBridge

__all__ = ("HookBridge",)


@dataclass(slots=True)
class HookBridge(BufferedCapabilityBridge):
    metadata_key: str | None = "hooks"
    hide_all: bool = False
    record_event_stream: bool = True
    record_model_requests: bool = True
    record_node_lifecycle: bool = True
    record_prepare_tools: bool = True
    record_run_lifecycle: bool = True
    record_tool_execution: bool = True
    record_tool_validation: bool = True

    def build_capability(self, session: AcpSessionContext) -> Hooks[Any]:
        async def before_run(ctx: RunContext[Any]) -> None:
            del ctx
            if self._run_lifecycle_enabled:
                self._record_completed_event(
                    session,
                    title="hook.before_run",
                    raw_output="run started",
                )

        async def wrap_run(
            ctx: RunContext[Any],
            *,
            handler: WrapRunHandler,
        ) -> AgentRunResult[Any]:
            del ctx
            try:
                result = await handler()
            except BaseException as error:
                if self._run_lifecycle_enabled:
                    self._record_failed_event(
                        session,
                        title="hook.wrap_run",
                        raw_output=str(error),
                    )
                raise
            if self._run_lifecycle_enabled:
                self._record_completed_event(
                    session,
                    title="hook.wrap_run",
                    raw_output="run wrapped",
                )
            return result

        async def after_run(
            ctx: RunContext[Any],
            *,
            result: AgentRunResult[Any],
        ) -> AgentRunResult[Any]:
            del ctx
            if self._run_lifecycle_enabled:
                self._record_completed_event(
                    session,
                    title="hook.after_run",
                    raw_output="run completed",
                )
            return result

        async def on_run_error(
            ctx: RunContext[Any],
            *,
            error: BaseException,
        ) -> AgentRunResult[Any]:
            del ctx
            if self._run_lifecycle_enabled:
                self._record_failed_event(
                    session,
                    title="hook.on_run_error",
                    raw_output=str(error),
                )
            raise error

        async def before_node_run(
            ctx: RunContext[Any],
            *,
            node: AgentNode[Any],
        ) -> AgentNode[Any]:
            del ctx
            if self._node_lifecycle_enabled:
                self._record_completed_event(
                    session,
                    title="hook.before_node_run",
                    raw_output=type(node).__name__,
                )
            return node

        async def after_node_run(
            ctx: RunContext[Any],
            *,
            node: AgentNode[Any],
            result: NodeResult[Any],
        ) -> NodeResult[Any]:
            del ctx
            if self._node_lifecycle_enabled:
                self._record_completed_event(
                    session,
                    title="hook.after_node_run",
                    raw_output=type(node).__name__,
                )
            return result

        async def wrap_node_run(
            ctx: RunContext[Any],
            *,
            node: AgentNode[Any],
            handler: WrapNodeRunHandler[Any],
        ) -> NodeResult[Any]:
            del ctx
            try:
                result = await handler(node)
            except Exception as error:
                if self._node_lifecycle_enabled:
                    self._record_failed_event(
                        session,
                        title="hook.wrap_node_run",
                        raw_output=str(error),
                    )
                raise
            if self._node_lifecycle_enabled:
                self._record_completed_event(
                    session,
                    title="hook.wrap_node_run",
                    raw_output=type(node).__name__,
                )
            return result

        async def on_event(
            ctx: RunContext[Any],
            event: AgentStreamEvent,
        ) -> AgentStreamEvent:
            del ctx
            if self._event_stream_enabled:
                event_kind = getattr(event, "event_kind", type(event).__name__)
                self._record_completed_event(
                    session,
                    title="hook.on_event",
                    raw_output=str(event_kind),
                )
            return event

        async def wrap_run_event_stream(
            ctx: RunContext[Any],
            *,
            stream: AsyncIterable[AgentStreamEvent],
        ) -> AsyncIterable[AgentStreamEvent]:
            del ctx
            if self._event_stream_enabled:
                self._record_completed_event(
                    session,
                    title="hook.wrap_run_event_stream",
                    raw_output="stream wrapped",
                )
            async for event in stream:
                yield event

        async def before_model_request(
            ctx: RunContext[Any],
            request_context: Any,
        ) -> Any:
            del ctx
            if self._model_requests_enabled:
                messages = getattr(request_context, "messages", [])
                self._record_completed_event(
                    session,
                    title="hook.before_model_request",
                    raw_output=f"messages={len(messages)}",
                )
            return request_context

        async def wrap_model_request(
            ctx: RunContext[Any],
            *,
            request_context: Any,
            handler: WrapModelRequestHandler,
        ) -> ModelResponse:
            del ctx
            try:
                response = await handler(request_context)
            except Exception as error:
                if self._model_requests_enabled:
                    self._record_failed_event(
                        session,
                        title="hook.wrap_model_request",
                        raw_output=str(error),
                    )
                raise
            if self._model_requests_enabled:
                self._record_completed_event(
                    session,
                    title="hook.wrap_model_request",
                    raw_output=f"messages={len(getattr(request_context, 'messages', []))}",
                )
            return response

        async def after_model_request(
            ctx: RunContext[Any],
            *,
            request_context: Any,
            response: ModelResponse,
        ) -> ModelResponse:
            del ctx, request_context
            if self._model_requests_enabled:
                self._record_completed_event(
                    session,
                    title="hook.after_model_request",
                    raw_output=f"parts={len(response.parts)}",
                )
            return response

        async def prepare_tools(
            ctx: RunContext[Any],
            tool_defs: list[ToolDefinition],
        ) -> list[ToolDefinition]:
            del ctx
            if self._prepare_tools_enabled:
                self._record_completed_event(
                    session,
                    title="hook.prepare_tools",
                    raw_output=f"tools={len(tool_defs)}",
                )
            return tool_defs

        async def before_tool_validate(
            ctx: RunContext[Any],
            *,
            call: ToolCallPart,
            tool_def: ToolDefinition,
            args: RawToolArgs,
        ) -> RawToolArgs:
            del ctx, tool_def
            if self._tool_validation_enabled:
                self._record_completed_event(
                    session,
                    title="hook.before_tool_validate",
                    raw_input={"args": args, "tool_name": call.tool_name},
                    raw_output=call.tool_name,
                )
            return args

        async def after_tool_validate(
            ctx: RunContext[Any],
            *,
            call: ToolCallPart,
            tool_def: ToolDefinition,
            args: ValidatedToolArgs,
        ) -> ValidatedToolArgs:
            del ctx, tool_def
            if self._tool_validation_enabled:
                self._record_completed_event(
                    session,
                    title="hook.after_tool_validate",
                    raw_input={"args": args, "tool_name": call.tool_name},
                    raw_output=call.tool_name,
                )
            return args

        async def wrap_tool_validate(
            ctx: RunContext[Any],
            *,
            call: ToolCallPart,
            tool_def: ToolDefinition,
            args: RawToolArgs,
            handler: WrapToolValidateHandler,
        ) -> ValidatedToolArgs:
            del ctx, tool_def
            try:
                validated_args = await handler(args)
            except Exception as error:
                if self._tool_validation_enabled:
                    self._record_failed_event(
                        session,
                        title="hook.wrap_tool_validate",
                        raw_input={"args": args, "tool_name": call.tool_name},
                        raw_output=str(error),
                    )
                raise
            if self._tool_validation_enabled:
                self._record_completed_event(
                    session,
                    title="hook.wrap_tool_validate",
                    raw_input={"args": args, "tool_name": call.tool_name},
                    raw_output=call.tool_name,
                )
            return validated_args

        async def before_tool_execute(
            ctx: RunContext[Any],
            *,
            call: ToolCallPart,
            tool_def: ToolDefinition,
            args: dict[str, Any],
        ) -> dict[str, Any]:
            del ctx, tool_def
            if self._tool_execution_enabled:
                self._record_completed_event(
                    session,
                    title="hook.before_tool_execute",
                    raw_input={
                        "args": args,
                        "tool_name": call.tool_name,
                    },
                    raw_output=call.tool_name,
                )
            return args

        async def wrap_tool_execute(
            ctx: RunContext[Any],
            *,
            call: ToolCallPart,
            tool_def: ToolDefinition,
            args: dict[str, Any],
            handler: WrapToolExecuteHandler,
        ) -> Any:
            del ctx, tool_def
            try:
                result = await handler(args)
            except Exception as error:
                if self._tool_execution_enabled:
                    self._record_failed_event(
                        session,
                        title="hook.wrap_tool_execute",
                        raw_input={"args": args, "tool_name": call.tool_name},
                        raw_output=str(error),
                    )
                raise
            if self._tool_execution_enabled:
                self._record_completed_event(
                    session,
                    title="hook.wrap_tool_execute",
                    raw_input={"args": args, "tool_name": call.tool_name},
                    raw_output=str(result),
                )
            return result

        async def after_tool_execute(
            ctx: RunContext[Any],
            *,
            call: ToolCallPart,
            tool_def: ToolDefinition,
            args: dict[str, Any],
            result: Any,
        ) -> Any:
            del ctx, tool_def
            if self._tool_execution_enabled:
                self._record_completed_event(
                    session,
                    title="hook.after_tool_execute",
                    raw_input={
                        "args": args,
                        "tool_name": call.tool_name,
                    },
                    raw_output=str(result),
                )
            return result

        async def on_tool_execute_error(
            ctx: RunContext[Any],
            *,
            call: ToolCallPart,
            tool_def: ToolDefinition,
            args: dict[str, Any],
            error: Exception,
        ) -> Any:
            del ctx, tool_def
            if self._tool_execution_enabled:
                self._record_failed_event(
                    session,
                    title="hook.on_tool_execute_error",
                    raw_input={
                        "args": args,
                        "tool_name": call.tool_name,
                    },
                    raw_output=str(error),
                )
            raise error

        hook_kwargs: dict[str, Any] = {}
        if self._model_requests_enabled:
            hook_kwargs.update(
                after_model_request=after_model_request,
                before_model_request=before_model_request,
                model_request=wrap_model_request,
            )
        if self._node_lifecycle_enabled:
            hook_kwargs.update(
                after_node_run=after_node_run,
                before_node_run=before_node_run,
                node_run=wrap_node_run,
            )
        if self._run_lifecycle_enabled:
            hook_kwargs.update(
                after_run=after_run,
                before_run=before_run,
                run=wrap_run,
                run_error=on_run_error,
            )
        if self._tool_validation_enabled:
            hook_kwargs.update(
                after_tool_validate=after_tool_validate,
                before_tool_validate=before_tool_validate,
                tool_validate=wrap_tool_validate,
            )
        if self._tool_execution_enabled:
            hook_kwargs.update(
                after_tool_execute=after_tool_execute,
                before_tool_execute=before_tool_execute,
                tool_execute=wrap_tool_execute,
                tool_execute_error=on_tool_execute_error,
            )
        if self._event_stream_enabled:
            hook_kwargs.update(
                event=on_event,
                run_event_stream=wrap_run_event_stream,
            )
        if self._prepare_tools_enabled:
            hook_kwargs["prepare_tools"] = prepare_tools
        return Hooks(**hook_kwargs)

    def get_session_metadata(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> dict[str, JsonValue]:
        del session, agent
        enabled: list[str] = []
        if self._run_lifecycle_enabled:
            enabled.extend(["before_run", "wrap_run", "after_run", "on_run_error"])
        if self._node_lifecycle_enabled:
            enabled.extend(["before_node_run", "wrap_node_run", "after_node_run"])
        if self._event_stream_enabled:
            enabled.extend(["wrap_run_event_stream", "on_event"])
        if self._model_requests_enabled:
            enabled.extend(["before_model_request", "wrap_model_request", "after_model_request"])
        if self._prepare_tools_enabled:
            enabled.append("prepare_tools")
        if self._tool_validation_enabled:
            enabled.extend(["before_tool_validate", "wrap_tool_validate", "after_tool_validate"])
        if self._tool_execution_enabled:
            enabled.extend(
                [
                    "before_tool_execute",
                    "wrap_tool_execute",
                    "after_tool_execute",
                    "on_tool_execute_error",
                ]
            )
        events: list[JsonValue] = list(enabled)
        return {"events": events}

    @property
    def _event_stream_enabled(self) -> bool:
        return not self.hide_all and self.record_event_stream

    @property
    def _model_requests_enabled(self) -> bool:
        return not self.hide_all and self.record_model_requests

    @property
    def _node_lifecycle_enabled(self) -> bool:
        return not self.hide_all and self.record_node_lifecycle

    @property
    def _prepare_tools_enabled(self) -> bool:
        return not self.hide_all and self.record_prepare_tools

    @property
    def _run_lifecycle_enabled(self) -> bool:
        return not self.hide_all and self.record_run_lifecycle

    @property
    def _tool_execution_enabled(self) -> bool:
        return not self.hide_all and self.record_tool_execution

    @property
    def _tool_validation_enabled(self) -> bool:
        return not self.hide_all and self.record_tool_validation
