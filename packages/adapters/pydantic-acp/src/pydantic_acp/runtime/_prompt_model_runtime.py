from __future__ import annotations as _annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager, nullcontext
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from acp.exceptions import RequestError
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai import models as pydantic_models
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.tools import DeferredToolRequests

from ..models import ModelOverride
from ..session.state import AcpSessionContext
from .hook_introspection import observe_agent_hooks

if TYPE_CHECKING:
    from ._prompt_runtime import NativePlanGeneration, RunOutputType, _PromptRuntime

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)
OutputDataT = TypeVar("OutputDataT", covariant=True)

__all__ = ("_PromptModelRuntime",)


class _PromptModelRuntime(Generic[AgentDepsT, OutputDataT]):
    def __init__(
        self,
        runtime: _PromptRuntime[AgentDepsT, OutputDataT],
        *,
        native_plan_type: type[NativePlanGeneration],
    ) -> None:
        self._runtime = runtime
        self._native_plan_type = native_plan_type

    def build_run_output_type(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        session: AcpSessionContext,
    ) -> RunOutputType | None:
        output_type: RunOutputType = agent.output_type
        if self._runtime._requires_native_plan_output(session):
            output_type = self._native_plan_type
        if not self._runtime._supports_deferred_approval_bridge():
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

    def should_stream_text_responses(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        model_override: ModelOverride | None,
        output_type: RunOutputType | None,
    ) -> bool:
        candidate_output_type = agent.output_type if output_type is None else output_type
        return self.contains_text_output(candidate_output_type) and self.supports_streaming_model(
            agent,
            model_override=model_override,
        )

    def contains_text_output(self, output_type: Any) -> bool:
        if output_type is str or output_type is self._native_plan_type:
            return True
        if isinstance(output_type, Sequence) and not isinstance(output_type, str):
            return any(self.contains_text_output(item) for item in output_type)
        return False

    def contains_native_plan_generation(self, output_type: Any) -> bool:
        if output_type is self._native_plan_type:
            return True
        if isinstance(output_type, Sequence) and not isinstance(output_type, str):
            return any(self.contains_native_plan_generation(item) for item in output_type)
        return False

    def supports_streaming_model(
        self,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        *,
        model_override: ModelOverride | None,
    ) -> bool:
        model = self.resolve_runtime_model(agent, model_override=model_override)
        if isinstance(model, FunctionModel):
            return model.stream_function is not None
        return type(model).request_stream is not pydantic_models.Model.request_stream

    def resolve_runtime_model(
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

    def text_chunk_from_event(self, event: Any) -> str | None:
        if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
            return event.part.content
        if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
            return event.delta.content_delta
        return None

    def hook_context(
        self,
        *,
        agent: PydanticAgent[AgentDepsT, OutputDataT],
        session: AcpSessionContext,
    ) -> AbstractContextManager[None]:
        if self._runtime._owner._config.hook_projection_map is None:
            return nullcontext()
        return observe_agent_hooks(
            agent,
            write_update=lambda update: self._runtime._owner._record_update(session, update),
            projection_map=self._runtime._owner._config.hook_projection_map,
        )

    def synchronize_native_plan_output(
        self,
        session: AcpSessionContext,
        output: Any,
        *,
        streamed_output: bool,
    ) -> str:
        if not isinstance(output, self._native_plan_type):
            return ""
        self._runtime._set_native_plan_state(
            session,
            entries=output.plan_entries,
            plan_markdown=output.plan_md,
        )
        if streamed_output:
            return ""
        return output.plan_md
