from __future__ import annotations as _annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeAlias

__all__ = ("write_auth_file",)

JsonPrimitive: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]


def _encode_segment(payload: dict[str, JsonValue]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8"))
    return encoded.decode("utf-8").rstrip("=")


def _jwt(claims: dict[str, JsonValue]) -> str:
    header = _encode_segment({"alg": "none", "typ": "JWT"})
    payload = _encode_segment(claims)
    return f"{header}.{payload}.signature"


def write_auth_file(
    path: Path,
    *,
    access_expiry: datetime | None = None,
    account_id: str = "acct_default",
    refresh_token: str = "refresh_token_value",
) -> None:
    now = datetime.now(tz=UTC)
    expires_at = access_expiry or (now + timedelta(hours=1))
    auth_claims: dict[str, JsonValue] = {"chatgpt_account_id": account_id}
    payload = {
        "OPENAI_API_KEY": None,
        "auth_mode": "oauth",
        "last_refresh": now.isoformat().replace("+00:00", "Z"),
        "tokens": {
            "access_token": _jwt(
                {
                    "exp": int(expires_at.timestamp()),
                    "https://api.openai.com/auth": auth_claims,
                    "sub": "user_123",
                }
            ),
            "account_id": account_id,
            "id_token": _jwt(
                {
                    "email": "demo@example.com",
                    "exp": int(expires_at.timestamp()),
                    "https://api.openai.com/auth": auth_claims,
                    "sub": "user_123",
                }
            ),
            "refresh_token": refresh_token,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
