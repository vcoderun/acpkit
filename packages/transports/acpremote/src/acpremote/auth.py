from __future__ import annotations as _annotations

from collections.abc import Mapping

__all__ = (
    "bearer_headers",
    "is_bearer_authorized",
    "normalized_bearer_token",
)


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
    expected_token = normalized_bearer_token(token)
    if expected_token is None:
        return True
    return headers.get("Authorization") == f"Bearer {expected_token}"
