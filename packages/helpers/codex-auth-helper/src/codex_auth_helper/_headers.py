from __future__ import annotations as _annotations

from collections.abc import Mapping
from typing import Final

import httpx

from ._version import __version__

CODEX_USER_AGENT: Final = f"codex-auth-helper/{__version__}"
RESPONSES_WEBSOCKET_BETA: Final = "responses_websockets=2026-02-06"


def codex_request_headers(
    headers: Mapping[str, str] | None = None,
    *,
    model: object = None,
    service_tier: object = None,
    websocket: bool = False,
) -> dict[str, str]:
    result = dict(httpx.Headers(headers or {}))
    result.setdefault("originator", "codex-auth-helper")
    # Pydantic adds its own User-Agent unless this canonical key is present.
    result["User-Agent"] = result.pop("user-agent", CODEX_USER_AGENT)
    if isinstance(model, str) and model:
        routing = f"model={model}"
        if isinstance(service_tier, str) and service_tier:
            routing += f";tier={service_tier}"
        result.setdefault("x-codex-routing-hint", routing)
    if websocket:
        result.setdefault("openai-beta", RESPONSES_WEBSOCKET_BETA)
    return result
