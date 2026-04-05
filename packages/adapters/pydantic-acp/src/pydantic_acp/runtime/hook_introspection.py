from __future__ import annotations as _annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

import anyio
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai import _utils
from pydantic_ai.capabilities import AbstractCapability, CombinedCapability, Hooks, HookTimeoutError
from pydantic_ai.capabilities.hooks import _HookEntry
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models import ModelRequestContext

from ..awaitables import resolve_value
from ..hook_projection import HookEvent, HookProjectionMap
from ..session.state import SessionTranscriptUpdate

__all__ = ("RegisteredHookInfo", "list_agent_hooks", "observe_agent_hooks")

_INTERNAL_EVENT_NAMES = {
    "_on_event": "event",
    "on_model_request_error": "model_request_error",
    "on_node_run_error": "node_run_error",
    "on_run_error": "run_error",
    "on_tool_execute_error": "tool_execute_error",
    "on_tool_validate_error": "tool_validate_error",
    "wrap_model_request": "model_request",
    "wrap_node_run": "node_run",
    "wrap_run": "run",
    "wrap_run_event_stream": "run_event_stream",
    "wrap_tool_execute": "tool_execute",
    "wrap_tool_validate": "tool_validate",
}
_SKIPPED_HOOK_MODULE = "pydantic_acp.bridges.hooks"


@dataclass(slots=True)
class _HookUpdateEmitter:
    write_update: Callable[[SessionTranscriptUpdate], Awaitable[None]]
    projection_map: HookProjectionMap
    run_id: str
    next_sequence_id: int = 1

    async def emit_start(
        self,
        *,
        event: HookEvent,
    ) -> str | None:
        tool_call_id = f"hook:{self.run_id}:{self.next_sequence_id}"
        self.next_sequence_id += 1
        start_update = self.projection_map.build_start_update(
            tool_call_id=tool_call_id,
            event=event,
        )
        if start_update is None:
            return None
        await self.write_update(start_update)
        return tool_call_id

    async def emit_progress(
        self,
        *,
        tool_call_id: str | None,
        event: HookEvent,
    ) -> None:
        if tool_call_id is None:
            return
        progress_update = self.projection_map.build_progress_update(
            tool_call_id=tool_call_id,
            event=event,
        )
        if progress_update is None:
            return
        await self.write_update(progress_update)

    async def emit(
        self,
        *,
        event: HookEvent,
    ) -> None:
        tool_call_id = await self.emit_start(event=event)
        await self.emit_progress(
            tool_call_id=tool_call_id,
            event=event,
        )


@dataclass(slots=True, frozen=True, kw_only=True)
class RegisteredHookInfo:
    event_id: str
    hook_name: str
    tool_filters: tuple[str, ...]


@contextmanager
def observe_agent_hooks(
    agent: PydanticAgent[Any, Any],
    *,
    write_update: Callable[[SessionTranscriptUpdate], Awaitable[None]],
    projection_map: HookProjectionMap | None = None,
) -> Iterator[None]:
    root_capability = _root_capability(agent)
    override_root_capability = _override_root_capability(agent)
    if root_capability is None or override_root_capability is None:
        yield
        return
    emitter = _HookUpdateEmitter(
        write_update=write_update,
        projection_map=projection_map or HookProjectionMap(),
        run_id=uuid4().hex,
    )
    wrapped_capability, changed = _wrap_combined_capability(
        root_capability,
        emitter=emitter,
    )
    if not changed:
        yield
        return
    token = override_root_capability.set(_utils.Some(wrapped_capability))
    try:
        yield
    finally:
        override_root_capability.reset(token)


def _root_capability(agent: PydanticAgent[Any, Any]) -> CombinedCapability[Any] | None:
    capability = getattr(agent, "_root_capability", None)
    if isinstance(capability, CombinedCapability):
        return capability
    return None


def list_agent_hooks(agent: PydanticAgent[Any, Any]) -> list[RegisteredHookInfo]:
    root_capability = _root_capability(agent)
    if root_capability is None:
        return []
    hook_infos: list[RegisteredHookInfo] = []
    for hooks in _iter_hooks(root_capability):
        registry = getattr(hooks, "_registry", None)
        if not isinstance(registry, dict):
            continue
        for registry_key, entries in registry.items():
            if not isinstance(registry_key, str) or not isinstance(entries, list):
                continue
            event_id = _INTERNAL_EVENT_NAMES.get(registry_key, registry_key)
            for entry in entries:
                if not isinstance(entry, _HookEntry):
                    continue
                func = getattr(entry, "func", None)
                if not callable(func):
                    continue
                if getattr(func, "__module__", "") == _SKIPPED_HOOK_MODULE:
                    continue
                hook_infos.append(
                    RegisteredHookInfo(
                        event_id=event_id,
                        hook_name=getattr(func, "__name__", "") or event_id,
                        tool_filters=_tool_filters(entry),
                    )
                )
    return sorted(
        hook_infos,
        key=lambda hook_info: (hook_info.event_id, hook_info.hook_name, hook_info.tool_filters),
    )


def _override_root_capability(
    agent: PydanticAgent[Any, Any],
) -> ContextVar[_utils.Option[CombinedCapability[Any]]] | None:
    override_capability = getattr(agent, "_override_root_capability", None)
    if isinstance(override_capability, ContextVar):
        return override_capability
    return None


def _wrap_capability(
    capability: AbstractCapability[Any],
    *,
    emitter: _HookUpdateEmitter,
) -> tuple[AbstractCapability[Any], bool]:
    if isinstance(capability, Hooks):
        return _wrap_hooks(capability, emitter=emitter)
    if isinstance(capability, CombinedCapability):
        changed = False
        wrapped_capabilities: list[AbstractCapability[Any]] = []
        for nested_capability in capability.capabilities:
            wrapped_capability, nested_changed = _wrap_capability(
                nested_capability,
                emitter=emitter,
            )
            wrapped_capabilities.append(wrapped_capability)
            changed = changed or nested_changed
        if not changed:
            return capability, False
        return replace(capability, capabilities=wrapped_capabilities), True
    return capability, False


def _iter_hooks(capability: AbstractCapability[Any]) -> Iterator[Hooks[Any]]:
    if isinstance(capability, Hooks):
        yield capability
        return
    if isinstance(capability, CombinedCapability):
        for nested_capability in capability.capabilities:
            yield from _iter_hooks(nested_capability)


def _wrap_combined_capability(
    capability: CombinedCapability[Any],
    *,
    emitter: _HookUpdateEmitter,
) -> tuple[CombinedCapability[Any], bool]:
    wrapped_capability, changed = _wrap_capability(capability, emitter=emitter)
    if not changed:
        return capability, False
    assert isinstance(wrapped_capability, CombinedCapability)
    return wrapped_capability, True


def _wrap_hooks(
    hooks: Hooks[Any],
    *,
    emitter: _HookUpdateEmitter,
) -> tuple[Hooks[Any], bool]:
    registry = getattr(hooks, "_registry", None)
    if not isinstance(registry, dict):
        return hooks, False
    wrapped_registry: dict[str, list[_HookEntry[Any]]] = {}
    changed = False
    for registry_key, entries in registry.items():
        if not isinstance(registry_key, str) or not isinstance(entries, list):
            return hooks, False
        wrapped_entries: list[_HookEntry[Any]] = []
        for entry in entries:
            if not isinstance(entry, _HookEntry):
                return hooks, False
            wrapped_entry, entry_changed = _wrap_hook_entry(
                registry_key,
                entry,
                emitter=emitter,
            )
            wrapped_entries.append(wrapped_entry)
            changed = changed or entry_changed
        wrapped_registry[registry_key] = wrapped_entries
    if not changed:
        return hooks, False
    wrapped_hooks = Hooks[Any]()
    wrapped_hooks._registry = wrapped_registry  # pyright: ignore[reportPrivateUsage]
    return wrapped_hooks, True


def _wrap_hook_entry(
    registry_key: str,
    entry: _HookEntry[Any],
    *,
    emitter: _HookUpdateEmitter,
) -> tuple[_HookEntry[Any], bool]:
    original_func = getattr(entry, "func", None)
    if not callable(original_func):
        return entry, False
    if getattr(original_func, "__module__", "") == _SKIPPED_HOOK_MODULE:
        return entry, False
    event_id = _INTERNAL_EVENT_NAMES.get(registry_key, registry_key)
    hook_name = getattr(original_func, "__name__", "") or event_id
    timeout = getattr(entry, "timeout", None)
    tool_filters = _tool_filters(entry)

    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        tool_name = _tool_name(kwargs)
        start_event = HookEvent(
            event_id=event_id,
            hook_name=hook_name,
            tool_name=tool_name,
            tool_filters=tool_filters,
        )
        tool_call_id = await emitter.emit_start(event=start_event)
        try:
            result = await _call_hook_func(
                original_func,
                *args,
                timeout=timeout,
                hook_name=event_id,
                **kwargs,
            )
        except BaseException as error:
            await emitter.emit_progress(
                tool_call_id=tool_call_id,
                event=HookEvent(
                    event_id=event_id,
                    hook_name=hook_name,
                    tool_name=tool_name,
                    tool_filters=tool_filters,
                    raw_output=_summarize_error(error),
                    status="failed",
                ),
            )
            raise
        await emitter.emit_progress(
            tool_call_id=tool_call_id,
            event=HookEvent(
                event_id=event_id,
                hook_name=hook_name,
                tool_name=tool_name,
                tool_filters=tool_filters,
                raw_output=_summarize_result(result),
                status="completed",
            ),
        )
        return result

    wrapped.__name__ = hook_name
    wrapped.__qualname__ = getattr(original_func, "__qualname__", hook_name)
    return replace(entry, func=wrapped, timeout=None), True


async def _call_hook_func(
    func: Callable[..., Any],
    *args: Any,
    timeout: float | None,
    hook_name: str,
    **kwargs: Any,
) -> Any:
    if timeout is None:
        return await resolve_value(func(*args, **kwargs))
    try:
        with anyio.fail_after(timeout):
            return await resolve_value(func(*args, **kwargs))
    except TimeoutError:
        raise HookTimeoutError(
            hook_name=hook_name,
            func_name=getattr(func, "__name__", repr(func)),
            timeout=timeout,
        ) from None


def _tool_filters(entry: _HookEntry[Any]) -> tuple[str, ...]:
    filters = getattr(entry, "tools", None)
    if isinstance(filters, frozenset):
        return tuple(sorted(filter_name for filter_name in filters if isinstance(filter_name, str)))
    return ()


def _tool_name(kwargs: dict[str, Any]) -> str | None:
    call = kwargs.get("call")
    tool_name = getattr(call, "tool_name", None)
    if isinstance(tool_name, str):
        return tool_name
    return None


def _summarize_error(error: BaseException) -> str:
    return str(error) or type(error).__name__


def _summarize_result(result: object) -> str:
    if result is None:
        return "completed"
    if isinstance(result, str):
        return result
    if isinstance(result, bool | int | float):
        return str(result)
    if isinstance(result, ModelRequestContext):
        return f"messages={len(result.messages)}"
    if isinstance(result, ModelResponse):
        return f"parts={len(result.parts)}"
    if isinstance(result, ToolCallPart):
        return result.tool_name
    return type(result).__name__
