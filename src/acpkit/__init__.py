from __future__ import annotations as _annotations

import importlib
from typing import Any

from ._version import __version__
from .compatibility import CompatibilityManifest, SurfaceOwner, SurfaceStatus, SurfaceSupport
from .runtime import (
    AcpKitError,
    MissingAdapterError,
    TargetRef,
    TargetResolutionError,
    UnsupportedAgentError,
    launch_command,
    launch_target,
    load_target,
    run_remote_addr,
    run_target,
    serve_target,
)

__all__ = (
    "AcpKitError",
    "CompatibilityManifest",
    "connect_acp",
    "MissingAdapterError",
    "SurfaceOwner",
    "SurfaceStatus",
    "SurfaceSupport",
    "TargetRef",
    "TargetResolutionError",
    "UnsupportedAgentError",
    "__version__",
    "launch_command",
    "launch_target",
    "load_target",
    "run_remote_addr",
    "run_target",
    "serve_acp",
    "serve_target",
)


def connect_acp(url: str, **kwargs: Any) -> Any:
    module = importlib.import_module("acpremote")
    return module.connect_acp(url, **kwargs)


def serve_acp(agent: Any, **kwargs: Any) -> Any:
    module = importlib.import_module("acpremote")
    return module.serve_acp(agent, **kwargs)
