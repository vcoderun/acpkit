from __future__ import annotations as _annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from importlib.util import find_spec
from typing import TYPE_CHECKING, Any, final

from typing_extensions import TypeIs

if TYPE_CHECKING:
    from pydantic_ai import Agent as PydanticAgent

AdapterMatcher = Callable[[object], bool]
AdapterRunner = Callable[[object], None]

__all__ = (
    "AdapterDefinition",
    "find_adapter_by_module_name",
    "find_matching_adapter",
    "installed_adapters",
    "is_pydantic_target",
)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class AdapterDefinition:
    adapter_id: str
    extra_name: str
    package_name: str
    related_modules: tuple[str, ...]
    target_matcher: AdapterMatcher
    target_runner: AdapterRunner

    def install_command(self) -> str:
        return f'uv pip install "acpkit[{self.extra_name}]"'

    def is_installed(self) -> bool:
        return find_spec(self.package_name) is not None

    def matches_target(self, target: object) -> bool:
        return self.target_matcher(target)

    def run_target(self, target: object) -> None:
        self.target_runner(target)


def installed_adapters() -> tuple[AdapterDefinition, ...]:
    return tuple(adapter for adapter in _ADAPTER_DEFINITIONS if adapter.is_installed())


def find_adapter_by_module_name(module_name: str | None) -> AdapterDefinition | None:
    if module_name is None:
        return None
    for adapter in _ADAPTER_DEFINITIONS:
        if module_name == adapter.package_name or module_name in adapter.related_modules:
            return adapter
    return None


def find_matching_adapter(target: object) -> AdapterDefinition | None:
    for adapter in _ADAPTER_DEFINITIONS:
        if adapter.matches_target(target):
            return adapter
    return None


def is_pydantic_target(target: object) -> TypeIs[PydanticAgent[Any, Any]]:
    if find_spec("pydantic_ai") is None:
        return False
    from pydantic_ai import Agent as PydanticAgent

    return isinstance(target, PydanticAgent)


def _run_pydantic_target(target: object) -> None:
    if not is_pydantic_target(target):
        raise TypeError("Expected a `pydantic_ai.Agent` target.")
    module = importlib.import_module("pydantic_acp")
    module.run_acp(target)


_ADAPTER_DEFINITIONS: tuple[AdapterDefinition, ...] = (
    AdapterDefinition(
        adapter_id="pydantic",
        extra_name="pydantic",
        package_name="pydantic_acp",
        related_modules=("pydantic_ai",),
        target_matcher=is_pydantic_target,
        target_runner=_run_pydantic_target,
    ),
)
