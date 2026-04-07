from __future__ import annotations as _annotations

from dataclasses import dataclass, field
from typing import TypeAlias, TypeVar

from pydantic_ai._history_processor import _HistoryProcessorAsync as HistoryProcessorNoContextAsync
from pydantic_ai._history_processor import (
    _HistoryProcessorAsyncWithCtx as HistoryProcessorWithContextAsync,
)
from pydantic_ai._history_processor import _HistoryProcessorSync as HistoryProcessorNoContextSync
from pydantic_ai._history_processor import (
    _HistoryProcessorSyncWithCtx as HistoryProcessorWithContextSync,
)
from pydantic_ai.messages import ModelMessage
from pydantic_ai.tools import RunContext

from ..agent_types import RuntimeAgent
from ..awaitables import resolve_value
from ..session.state import AcpSessionContext, JsonValue
from .base import BufferedCapabilityBridge

AgentDepsT = TypeVar("AgentDepsT", contravariant=True)
ModelMessages: TypeAlias = list[ModelMessage]
HistoryProcessorPlain: TypeAlias = HistoryProcessorNoContextSync | HistoryProcessorNoContextAsync
HistoryProcessorContextual: TypeAlias = (
    HistoryProcessorWithContextSync[AgentDepsT] | HistoryProcessorWithContextAsync[AgentDepsT]
)

__all__ = (
    "HistoryProcessorBridge",
    "HistoryProcessorContextual",
    "HistoryProcessorPlain",
    "HistoryProcessorWithContextAsync",
    "HistoryProcessorWithContextSync",
)


@dataclass(slots=True)
class HistoryProcessorBridge(BufferedCapabilityBridge):
    metadata_key: str | None = "history_processors"
    processor_names: list[str] = field(default_factory=list)

    def wrap_plain_processor(
        self,
        session: AcpSessionContext,
        processor: HistoryProcessorPlain,
        *,
        name: str = "history_processor",
    ) -> HistoryProcessorNoContextAsync:
        self._register_processor_name(name)

        async def wrapped(messages: ModelMessages) -> ModelMessages:
            return await self._run_plain_processor(
                session,
                processor,
                name=name,
                messages=messages,
            )

        return wrapped

    def wrap_contextual_processor(
        self,
        session: AcpSessionContext,
        processor: HistoryProcessorContextual[AgentDepsT],
        *,
        name: str = "history_processor",
    ) -> HistoryProcessorWithContextAsync[AgentDepsT]:
        self._register_processor_name(name)

        async def wrapped(
            ctx: RunContext[AgentDepsT],
            messages: ModelMessages,
        ) -> ModelMessages:
            return await self._run_contextual_processor(
                session,
                processor,
                name=name,
                messages=messages,
                ctx=ctx,
            )

        return wrapped

    def get_session_metadata(
        self,
        session: AcpSessionContext,
        agent: RuntimeAgent,
    ) -> dict[str, JsonValue]:
        del session, agent
        processors: list[JsonValue] = list(self.processor_names)
        return {"processors": processors}

    def _register_processor_name(self, name: str) -> None:
        if name not in self.processor_names:
            self.processor_names.append(name)

    async def _run_contextual_processor(
        self,
        session: AcpSessionContext,
        processor: HistoryProcessorContextual[AgentDepsT],
        *,
        name: str,
        ctx: RunContext[AgentDepsT],
        messages: ModelMessages,
    ) -> ModelMessages:
        try:
            result = processor(ctx, messages)
            processed = await resolve_value(result)
        except Exception as error:
            self._record_failed_event(
                session,
                title=f"history_processor.{name}",
                raw_output=str(error),
            )
            raise

        self._record_completed_event(
            session,
            title=f"history_processor.{name}",
            raw_output=f"messages={len(messages)}->{len(processed)}",
        )
        return processed

    async def _run_plain_processor(
        self,
        session: AcpSessionContext,
        processor: HistoryProcessorPlain,
        *,
        name: str,
        messages: ModelMessages,
    ) -> ModelMessages:
        try:
            result = processor(messages)
            processed = await resolve_value(result)
        except Exception as error:
            self._record_failed_event(
                session,
                title=f"history_processor.{name}",
                raw_output=str(error),
            )
            raise

        self._record_completed_event(
            session,
            title=f"history_processor.{name}",
            raw_output=f"messages={len(messages)}->{len(processed)}",
        )
        return processed
