from __future__ import annotations as _annotations

import importlib
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, final

from .adapters import (
    AdapterDefinition,
    find_adapter_by_module_name,
    find_matching_adapter,
    installed_adapters,
    is_pydantic_target,
)

__all__ = (
    "AcpKitError",
    "MissingAdapterError",
    "TargetRef",
    "TargetResolutionError",
    "UnsupportedAgentError",
    "load_target",
    "run_target",
)

if TYPE_CHECKING:
    from pydantic_ai import Agent as PydanticAgent

    PydanticAgentRunner = Callable[[PydanticAgent[Any, Any]], None]
else:
    PydanticAgentRunner = Callable[[object], None]


class AcpKitError(RuntimeError):
    """Base error for root acpkit runtime failures."""


class TargetResolutionError(AcpKitError):
    """Raised when a `module:attr` target cannot be resolved."""


class MissingAdapterError(AcpKitError):
    """Raised when a known target requires an adapter extra that is not installed."""

    @classmethod
    def for_adapter(cls, adapter: AdapterDefinition) -> MissingAdapterError:
        return cls(
            f"The resolved target requires the `{adapter.extra_name}` adapter. "
            f"Install it with: `{adapter.install_command()}`."
        )

    @classmethod
    def for_any_adapter(cls) -> MissingAdapterError:
        example_command = 'uv pip install "acpkit[pydantic]"'
        return cls(f"No ACP adapters are installed. Install one, for example: `{example_command}`.")


class UnsupportedAgentError(AcpKitError):
    """Raised when no installed adapter supports the resolved target."""


@final
@dataclass(frozen=True, slots=True)
class TargetRef:
    module_name: str
    attribute_path: str | None


def parse_target_ref(target: str) -> TargetRef:
    module_name, separator, attribute_path = target.partition(":")
    if not module_name:
        raise TargetResolutionError(
            "Target must include a module name, for example `my_app` or `my_app:agent`."
        )
    if separator != "" and not attribute_path:
        raise TargetResolutionError(
            "Target attribute cannot be empty. Use `my_app` or `my_app:agent`."
        )
    return TargetRef(
        module_name=module_name,
        attribute_path=attribute_path if separator != "" else None,
    )


def load_target(target: str, *, import_roots: Sequence[str] | None = None) -> object:
    reference = parse_target_ref(target)
    module = _import_target_module(reference, target=target, import_roots=import_roots)
    return _resolve_target_from_module(module, reference, target)


def run_target(
    target: str,
    *,
    import_roots: Sequence[str] | None = None,
    pydantic_runner: PydanticAgentRunner | None = None,
) -> None:
    reference = parse_target_ref(target)
    module = _import_target_module(reference, target=target, import_roots=import_roots)
    loaded_target = _resolve_target_from_module(module, reference, target)
    adapter = find_matching_adapter(loaded_target)
    if adapter is None:
        if not installed_adapters():
            raise MissingAdapterError.for_any_adapter()
        raise UnsupportedAgentError("No installed adapter supports the resolved target.")

    if adapter.adapter_id == "pydantic" and pydantic_runner is not None:
        if is_pydantic_target(loaded_target):
            pydantic_runner(loaded_target)
            return
        raise UnsupportedAgentError("Expected a `pydantic_ai.Agent` instance.")

    if not adapter.is_installed():
        raise MissingAdapterError.for_adapter(adapter)
    adapter.run_target(loaded_target)


def _missing_adapter_from_import_error(exc: ImportError) -> AdapterDefinition | None:
    missing_module = getattr(exc, "name", None)
    adapter = find_adapter_by_module_name(missing_module)
    if adapter is None or adapter.is_installed():
        return None
    return adapter


def _import_target_module(
    reference: TargetRef,
    *,
    target: str,
    import_roots: Sequence[str] | None,
) -> ModuleType:
    _ensure_import_root(Path.cwd())
    for import_root in import_roots or []:
        _ensure_import_root(Path(import_root))
    importlib.invalidate_caches()
    try:
        return importlib.import_module(reference.module_name)
    except ImportError as exc:
        missing_adapter = _missing_adapter_from_import_error(exc)
        if missing_adapter is not None:
            raise MissingAdapterError.for_adapter(missing_adapter) from exc
        raise TargetResolutionError(
            f"Could not import module `{reference.module_name}` from target `{target}`."
        ) from exc


def _resolve_latest_supported_target(module: ModuleType, target: str) -> object:
    latest_target: object | None = None
    for value in vars(module).values():
        if find_matching_adapter(value) is not None:
            latest_target = value
    if latest_target is None:
        raise UnsupportedAgentError(
            f"Target `{target}` did not resolve to a supported agent and the module defines no "
            "known agent instance."
        )
    return latest_target


def _resolve_target_from_module(module: ModuleType, reference: TargetRef, target: str) -> object:
    if reference.attribute_path is None:
        return _resolve_latest_supported_target(module, target)

    value: object = module
    for attribute_name in reference.attribute_path.split("."):
        try:
            value = getattr(value, attribute_name)
        except AttributeError as exc:
            raise TargetResolutionError(
                f"Target `{target}` is missing attribute `{attribute_name}`."
            ) from exc
    return value


def _ensure_import_root(path: Path) -> None:
    root_path = path.parent if path.exists() and path.is_file() else path
    resolved_path = str(root_path.resolve())
    if resolved_path not in sys.path:
        sys.path.insert(0, resolved_path)
