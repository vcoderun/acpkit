from __future__ import annotations as _annotations

from collections.abc import Mapping
from hmac import compare_digest

__all__ = (
    "bearer_headers",
    "is_bearer_authorized",
    "normalized_bearer_token",
)


_BEARER_SUBPROTOCOL_PREFIX = "bearer."


def bearer_headers(token: str | None) -> dict[str, str] | None:
    stripped = normalized_bearer_token(token)
    if stripped is None:
        return None
    return {"Authorization": f"Bearer {stripped}"}


def normalized_bearer_token(token: str | None) -> str | None:
    if token is None:
        return None
    stripped = token.strip()
    if not stripped:
        return None
    return stripped


def is_bearer_authorized(
    headers: Mapping[str, str],
    token: str | None,
) -> bool:
    """Check the ``Authorization`` header, then ``bearer.<token>`` WebSocket subprotocols.

    Browser WebSocket clients cannot set custom headers, so they carry the token as an extra
    ``Sec-WebSocket-Protocol`` entry next to ``acp.v1``.
    """
    expected_token = normalized_bearer_token(token)
    if expected_token is None:
        return True
    authorization = headers.get("Authorization")
    if authorization is not None and compare_digest(
        authorization.encode(),
        f"Bearer {expected_token}".encode(),
    ):
        return True
    return any(
        compare_digest(candidate.encode(), expected_token.encode())
        for candidate in _subprotocol_bearer_tokens(headers)
    )


def _subprotocol_bearer_tokens(headers: Mapping[str, str]) -> list[str]:
    try:
        raw_subprotocols = headers.get("Sec-WebSocket-Protocol")
    except LookupError:
        return []
    if raw_subprotocols is None:
        return []
    return [
        entry.removeprefix(_BEARER_SUBPROTOCOL_PREFIX)
        for entry in (part.strip() for part in raw_subprotocols.split(","))
        if entry.startswith(_BEARER_SUBPROTOCOL_PREFIX)
    ]
