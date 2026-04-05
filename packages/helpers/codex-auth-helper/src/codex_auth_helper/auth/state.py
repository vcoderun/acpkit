from __future__ import annotations as _annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

__all__ = ("CodexAuthState",)

_AUTH_CLAIMS_KEY: Final[str] = "https://api.openai.com/auth"
_ORGANIZATIONS_KEY: Final[str] = "organizations"


def _require_str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Expected a non-empty string for `{key}`.")
    return value


def _optional_str(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def _as_string_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None

    normalized: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str):
            normalized[key] = item
    return normalized


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone(UTC)


def _encode_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_jwt_claims(token: str) -> dict[str, object] | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")))
    except (ValueError, json.JSONDecodeError):
        return None
    return _as_string_mapping(claims)


def _extract_account_id_from_claims(claims: Mapping[str, object]) -> str | None:
    direct_account_id = claims.get("chatgpt_account_id")
    if isinstance(direct_account_id, str) and direct_account_id:
        return direct_account_id

    auth_claims = claims.get(_AUTH_CLAIMS_KEY)
    auth_mapping = _as_string_mapping(auth_claims)
    if auth_mapping is not None:
        nested_account_id = auth_mapping.get("chatgpt_account_id")
        if isinstance(nested_account_id, str) and nested_account_id:
            return nested_account_id

    organizations = claims.get(_ORGANIZATIONS_KEY)
    if isinstance(organizations, list) and organizations:
        organization = _as_string_mapping(organizations[0])
        if organization is not None:
            organization_id = organization.get("id")
            if isinstance(organization_id, str) and organization_id:
                return organization_id

    return None


def _extract_account_id(
    *, access_token: str, account_id: str | None, id_token: str | None
) -> str | None:
    if account_id is not None:
        return account_id

    if id_token is not None:
        id_claims = _parse_jwt_claims(id_token)
        if id_claims is not None:
            id_account_id = _extract_account_id_from_claims(id_claims)
            if id_account_id is not None:
                return id_account_id

    access_claims = _parse_jwt_claims(access_token)
    if access_claims is None:
        return None
    return _extract_account_id_from_claims(access_claims)


def _extract_expiry(*, access_token: str, id_token: str | None) -> datetime | None:
    for token in (id_token, access_token):
        if token is None:
            continue
        claims = _parse_jwt_claims(token)
        if claims is None:
            continue
        exp = claims.get("exp")
        if isinstance(exp, int):
            return datetime.fromtimestamp(exp, tz=UTC)
    return None


@dataclass(frozen=True, slots=True)
class CodexAuthState:
    access_token: str
    refresh_token: str
    account_id: str | None = None
    auth_mode: str | None = None
    expires_at: datetime | None = None
    id_token: str | None = None
    last_refresh: datetime | None = None
    openai_api_key: str | None = None

    @classmethod
    def from_json_dict(cls, data: dict[str, object]) -> CodexAuthState:
        tokens = _as_string_mapping(data.get("tokens"))
        if tokens is None:
            raise ValueError("Expected `tokens` to be an object.")

        access_token = _require_str(tokens, "access_token")
        id_token = _optional_str(tokens, "id_token")

        return cls(
            access_token=access_token,
            refresh_token=_require_str(tokens, "refresh_token"),
            account_id=_extract_account_id(
                access_token=access_token,
                account_id=_optional_str(tokens, "account_id"),
                id_token=id_token,
            ),
            auth_mode=_optional_str(data, "auth_mode"),
            expires_at=_extract_expiry(access_token=access_token, id_token=id_token),
            id_token=id_token,
            last_refresh=_parse_timestamp(_optional_str(data, "last_refresh")),
            openai_api_key=_optional_str(data, "OPENAI_API_KEY"),
        )

    def to_json_dict(self) -> dict[str, object]:
        return {
            "OPENAI_API_KEY": self.openai_api_key,
            "auth_mode": self.auth_mode,
            "last_refresh": _encode_timestamp(self.last_refresh),
            "tokens": {
                "access_token": self.access_token,
                "account_id": self.account_id,
                "id_token": self.id_token,
                "refresh_token": self.refresh_token,
            },
        }
