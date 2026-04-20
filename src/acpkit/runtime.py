from __future__ import annotations as _annotations

import asyncio
import importlib
import os
import shlex
import subprocess
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
    is_acp_target,
    is_langchain_target,
    is_pydantic_target,
)

__all__ = (
    "AcpKitError",
    "MissingAdapterError",
    "TargetRef",
    "TargetResolutionError",
    "UnsupportedAgentError",
    "launch_command",
    "load_target",
    "launch_target",
    "run_remote_addr",
    "run_target",
    "serve_target",
)

if TYPE_CHECKING:
    from acp.interfaces import Agent as AcpAgent
    from pydantic_ai import Agent as PydanticAgent

    AcpAgentRunner = Callable[[AcpAgent], None]
    PydanticAgentRunner = Callable[[PydanticAgent[Any, Any]], None]
else:
    AcpAgentRunner = Callable[[Any], None]
    PydanticAgentRunner = Callable[[Any], None]


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


def load_target(target: str, *, import_roots: Sequence[str] | None = None) -> Any:
    reference = parse_target_ref(target)
    module = _import_target_module(reference, target=target, import_roots=import_roots)
    return _resolve_target_from_module(module, reference, target)


def run_target(
    target: str,
    *,
    import_roots: Sequence[str] | None = None,
    acp_runner: AcpAgentRunner | None = None,
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

    if adapter.adapter_id == "acp" and acp_runner is not None:
        if is_acp_target(loaded_target):
            acp_runner(loaded_target)
            return
        raise UnsupportedAgentError("Expected an `acp.interfaces.Agent` instance.")

    if adapter.adapter_id == "pydantic" and pydantic_runner is not None:
        if is_pydantic_target(loaded_target):
            pydantic_runner(loaded_target)
            return
        raise UnsupportedAgentError("Expected a `pydantic_ai.Agent` instance.")

    if not adapter.is_installed():
        raise MissingAdapterError.for_adapter(adapter)
    adapter.run_target(loaded_target)


def launch_target(
    target: str,
    *,
    import_roots: Sequence[str] | None = None,
) -> int:
    mirrored_command = _build_mirrored_run_command(target, import_roots=import_roots)
    return launch_command(mirrored_command)


def launch_command(command: str) -> int:
    normalized_command = command.strip()
    if not normalized_command:
        raise TargetResolutionError("Launch command cannot be empty.")
    launch_command = [
        "uvx",
        "--python",
        "3.14",
        "--from",
        "batrachian-toad",
        "toad",
        "acp",
        normalized_command,
    ]
    try:
        completed = subprocess.run(launch_command, check=False)
    except FileNotFoundError as exc:
        raise AcpKitError(
            "`uvx` is required to launch Toad ACP sessions. "
            'Install it with: `uv pip install "acpkit[launch]"`.'
        ) from exc
    return completed.returncode


def run_remote_addr(addr: str, *, token_env: str | None = None) -> None:
    remote_module = _load_remote_module()
    acp_module = importlib.import_module("acp")
    bearer_token = _resolve_token_env(token_env)
    agent = remote_module.connect_acp(addr, bearer_token=bearer_token)
    asyncio.run(acp_module.run_agent(agent))


def serve_target(
    target: str,
    *,
    import_roots: Sequence[str] | None = None,
    host: str = "127.0.0.1",
    port: int = 8080,
    mount_path: str = "/acp",
    token_env: str | None = None,
) -> None:
    remote_module = _load_remote_module()
    loaded_target = load_target(target, import_roots=import_roots)
    acp_agent = _materialize_acp_agent(loaded_target)
    bearer_token = _resolve_token_env(token_env)

    async def _serve() -> None:
        server = await remote_module.serve_acp(
            acp_agent,
            host=host,
            port=port,
            mount_path=mount_path,
            bearer_token=bearer_token,
        )
        try:
            await server.serve_forever()
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(_serve())


def _missing_adapter_from_import_error(exc: ImportError) -> AdapterDefinition | None:
    missing_module = getattr(exc, "name", None)
    adapter = find_adapter_by_module_name(missing_module)
    if adapter is None or adapter.is_installed():
        return None
    return adapter


def _load_remote_module() -> Any:
    remote_adapter = find_adapter_by_module_name("acpremote")
    if remote_adapter is None:
        raise AcpKitError("acpremote adapter metadata is not registered.")
    if not remote_adapter.is_installed():
        raise MissingAdapterError.for_adapter(remote_adapter)
    return importlib.import_module("acpremote")


def _resolve_token_env(token_env: str | None) -> str | None:
    if token_env is None:
        return None
    token = os.getenv(token_env, "").strip()
    if token:
        return token
    raise AcpKitError(f"`{token_env}` is not set or is empty.")


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


def _resolve_latest_supported_target(module: ModuleType, target: str) -> Any:
    latest_target: Any | None = None
    for value in vars(module).values():
        if find_matching_adapter(value) is not None:
            latest_target = value
    if latest_target is None:
        raise UnsupportedAgentError(
            f"Target `{target}` did not resolve to a supported agent and the module defines no "
            "known agent instance."
        )
    return latest_target


def _materialize_acp_agent(target: Any) -> Any:
    if is_acp_target(target):
        return target
    if is_pydantic_target(target):
        module = importlib.import_module("pydantic_acp")
        return module.create_acp_agent(target)
    if is_langchain_target(target):
        module = importlib.import_module("langchain_acp")
        return module.create_acp_agent(graph=target)
    if not installed_adapters():
        raise MissingAdapterError.for_any_adapter()
    raise UnsupportedAgentError("No installed adapter supports the resolved target.")


def _resolve_target_from_module(module: ModuleType, reference: TargetRef, target: str) -> Any:
    if reference.attribute_path is None:
        return _resolve_latest_supported_target(module, target)

    value: Any = module
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


def _build_mirrored_run_command(
    target: str,
    *,
    import_roots: Sequence[str] | None,
) -> str:
    command = ["acpkit", "run", target]
    for import_root in import_roots or ():
        command.extend(("-p", import_root))
    return shlex.join(command)
