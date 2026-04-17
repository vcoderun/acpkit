from __future__ import annotations as _annotations

from contextvars import ContextVar
from copy import copy
from dataclasses import dataclass, is_dataclass, replace
from typing import Any, cast

from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.capabilities import CombinedCapability, Hooks

__all__ = (
    "build_capability_override_value",
    "entry_func",
    "entry_timeout",
    "entry_tool_filters",
    "hook_registry",
    "replace_hook_entry",
    "root_capability",
    "root_capability_override",
)


@dataclass(slots=True, frozen=True)
class _CapabilityOverrideValue:
    value: CombinedCapability[Any]


def build_capability_override_value(
    capability: CombinedCapability[Any],
) -> object:
    return _CapabilityOverrideValue(capability)


def root_capability(
    agent: PydanticAgent[Any, Any],
) -> CombinedCapability[Any] | None:
    capability = getattr(agent, "_root_capability", None)
    if isinstance(capability, CombinedCapability):
        return capability
    return None


def root_capability_override(
    agent: PydanticAgent[Any, Any],
) -> ContextVar[Any] | None:
    override_capability = getattr(agent, "_override_root_capability", None)
    if isinstance(override_capability, ContextVar):
        return override_capability
    return None


def hook_registry(hooks: Hooks[Any]) -> dict[str, list[Any]] | None:
    registry = getattr(hooks, "_registry", None)
    if not isinstance(registry, dict):
        return None
    normalized_registry: dict[str, list[Any]] = {}
    for key, entries in registry.items():
        if not isinstance(key, str) or not isinstance(entries, list):
            continue
        normalized_registry[key] = entries
    return normalized_registry


def entry_func(entry: object) -> Any:
    return getattr(entry, "func", None)


def entry_timeout(entry: object) -> float | None:
    timeout = getattr(entry, "timeout", None)
    if isinstance(timeout, float | int):
        return float(timeout)
    return None


def entry_tool_filters(entry: object) -> tuple[str, ...]:
    filters = getattr(entry, "tools", None)
    if isinstance(filters, frozenset):
        return tuple(sorted(filter_name for filter_name in filters if isinstance(filter_name, str)))
    return ()


def replace_hook_entry(
    entry: object,
    *,
    func: Any,
    timeout: float | None,
) -> object | None:
    if is_dataclass(entry):
        try:
            return replace(cast(Any, entry), func=func, timeout=timeout)
        except TypeError:
            return None
    try:
        wrapped_entry = copy(entry)
    except Exception:
        return None
    try:
        wrapped_entry_any = cast(Any, wrapped_entry)
        wrapped_entry_any.func = func
        wrapped_entry_any.timeout = timeout
    except Exception:
        return None
    return wrapped_entry
