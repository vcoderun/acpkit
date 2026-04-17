from __future__ import annotations as _annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, cast

from pydantic_acp.runtime._compat import (
    build_capability_override_value,
    entry_func,
    entry_timeout,
    entry_tool_filters,
    hook_registry,
    replace_hook_entry,
    root_capability,
    root_capability_override,
)
from pydantic_ai import Agent
from pydantic_ai.capabilities import CombinedCapability, Hooks
from pydantic_ai.models.test import TestModel


@dataclass
class _DataclassEntry:
    func: Any
    timeout: float | None
    tools: frozenset[str] | None = None


@dataclass
class _IncompatibleDataclassEntry:
    func: Any


class _MutableEntry:
    def __init__(self, func: Any, timeout: float | None) -> None:
        self.func = func
        self.timeout = timeout
        self.tools = frozenset({"b", "a"})


class _UncopyableEntry:
    def __copy__(self) -> _UncopyableEntry:
        raise RuntimeError("no copy")


class _ReadOnlyEntry:
    __slots__ = ("_func", "_timeout")

    def __init__(self, func: Any, timeout: float | None) -> None:
        self._func = func
        self._timeout = timeout

    @property
    def func(self) -> Any:
        return self._func

    @property
    def timeout(self) -> float | None:
        return self._timeout


def test_root_capability_and_override_helpers_handle_missing_and_present_private_attrs() -> None:
    agent = Agent(TestModel(custom_output_text="ok"))
    capability = CombinedCapability(capabilities=[])
    override = ContextVar[object]("override")

    cast(Any, agent)._root_capability = capability
    cast(Any, agent)._override_root_capability = override

    override_value = build_capability_override_value(capability)

    assert root_capability(agent) is capability
    assert root_capability_override(agent) is override
    assert type(override_value).__name__ == "_CapabilityOverrideValue"
    assert cast(Any, override_value).value is capability

    cast(Any, agent)._root_capability = "wrong"
    cast(Any, agent)._override_root_capability = "wrong"
    assert root_capability(agent) is None
    assert root_capability_override(agent) is None


def test_hook_registry_and_entry_helpers_normalize_supported_shapes() -> None:
    hooks = Hooks[Any]()
    valid_entry = _DataclassEntry(
        func="callable",
        timeout=1,
        tools=cast(Any, frozenset({"b", "a", 1})),
    )
    cast(Any, hooks)._registry = {
        "before_run": [valid_entry],
        1: [valid_entry],
        "bad": "wrong",
    }

    registry = hook_registry(hooks)

    assert registry == {"before_run": [valid_entry]}
    assert entry_func(valid_entry) == "callable"
    assert entry_timeout(valid_entry) == 1.0
    assert entry_tool_filters(valid_entry) == ("a", "b")
    assert entry_timeout(object()) is None
    assert entry_tool_filters(object()) == ()

    cast(Any, hooks)._registry = "wrong"
    assert hook_registry(hooks) is None


def test_replace_hook_entry_supports_dataclass_copy_and_failure_paths() -> None:
    dataclass_entry = _DataclassEntry(func="old", timeout=1.0)
    replaced_dataclass = replace_hook_entry(dataclass_entry, func="new", timeout=2.0)
    assert isinstance(replaced_dataclass, _DataclassEntry)
    assert replaced_dataclass.func == "new"
    assert replaced_dataclass.timeout == 2.0

    incompatible_dataclass = _IncompatibleDataclassEntry(func="old")
    assert replace_hook_entry(incompatible_dataclass, func="new", timeout=2.0) is None

    mutable_entry = _MutableEntry(func="old", timeout=1.0)
    replaced_mutable = replace_hook_entry(mutable_entry, func="new", timeout=2.0)
    assert isinstance(replaced_mutable, _MutableEntry)
    assert replaced_mutable is not mutable_entry
    assert replaced_mutable.func == "new"
    assert replaced_mutable.timeout == 2.0

    assert replace_hook_entry(_UncopyableEntry(), func="new", timeout=2.0) is None
    assert (
        replace_hook_entry(_ReadOnlyEntry(func="old", timeout=1.0), func="new", timeout=2.0) is None
    )
